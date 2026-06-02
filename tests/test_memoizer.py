import json
import pytest
from pathlib import Path
import runstate
from runstate.memoizer import history, launch_producer, ensure
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


def _cell(channel, *, run_id, up_to, ckpt_dir):
    """A resumable worker: reads its run_id-keyed checkpoint, continues the
    run-absolute step, checkpoints the new frontier. Subscription-gated."""
    ckpt = Path(ckpt_dir) / f"{run_id}.json"
    start = json.loads(ckpt.read_text())["next"] if ckpt.exists() else 0
    with runstate.Worker(channel, now=lambda: 0.0) as w:
        for step in w.steps(start=start, total=up_to):
            w.set("loss", float(step))
    ckpt.write_text(json.dumps({"next": up_to}))


def _producer(launcher, tmp_path, run_id="exp"):
    variant = runstate.Variant(
        run_id, _cell, {"kwargs": {"run_id": run_id, "ckpt_dir": str(tmp_path)}}
    )
    # pre-stage the loss subscription on the shared log; each episode drains it
    launcher.open_channel(run_id).send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="obs"
    )
    return launch_producer(launcher, variant)   # target_key="up_to"


def test_ensure_cold_miss_then_hit(tmp_path):
    launcher = runstate.ThreadLauncher()
    producer = _producer(launcher, tmp_path)
    series = ensure(producer, "loss", up_to=5)
    assert [b["step"] for b in series] == [0, 1, 2, 3, 4]
    launched = len(launcher.open_channel("exp").read(topics=["launcher.launched"]))
    series2 = ensure(producer, "loss", up_to=5)               # hit: no relaunch
    assert [b["step"] for b in series2] == [0, 1, 2, 3, 4]
    assert len(launcher.open_channel("exp").read(topics=["launcher.launched"])) == launched


def test_ensure_extends_partial_prefix_into_one_series(tmp_path):
    launcher = runstate.ThreadLauncher()
    producer = _producer(launcher, tmp_path)
    ensure(producer, "loss", up_to=3)                         # ep1: 0,1,2
    series = ensure(producer, "loss", up_to=6)                # ep2 resumes: 3,4,5
    assert [b["step"] for b in series] == [0, 1, 2, 3, 4, 5]  # one continuous series


def test_ensure_surfaces_a_failure_outcome(tmp_path):
    launcher = runstate.ThreadLauncher()

    def crash(channel, *, up_to):
        with runstate.Worker(channel, now=lambda: 0.0) as w:
            for step in w.steps(total=up_to):
                raise RuntimeError("boom")

    variant = runstate.Variant("exp", crash, {"kwargs": {}})
    producer = launch_producer(launcher, variant)
    with pytest.raises(RuntimeError, match="failed"):
        ensure(producer, "loss", up_to=5)


def test_ensure_raises_when_run_makes_no_progress(tmp_path):
    launcher = runstate.ThreadLauncher()

    def stuck(channel, *, up_to):                  # ignores up_to; always 2 steps, no ckpt
        with runstate.Worker(channel, now=lambda: 0.0) as w:
            for step in w.steps(total=2):
                w.set("loss", float(step))

    variant = runstate.Variant("exp", stuck, {"kwargs": {}})
    launcher.open_channel("exp").send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="obs"
    )
    producer = launch_producer(launcher, variant)
    with pytest.raises(RuntimeError, match="progress"):
        ensure(producer, "loss", up_to=5)


def test_ensure_redrives_within_one_call_to_reach_target(tmp_path):
    # A worker that advances at most 3 steps per episode: one ensure() call must
    # re-drive across several episodes (each drove=True, progress advancing) to
    # reach the target -- exercising the clean-stop-below-target re-drive path.
    launcher = runstate.ThreadLauncher()

    def chunked(channel, *, run_id, up_to, ckpt_dir):
        ckpt = Path(ckpt_dir) / f"{run_id}.json"
        start = json.loads(ckpt.read_text())["next"] if ckpt.exists() else 0
        stop = min(up_to, start + 3)
        with runstate.Worker(channel, now=lambda: 0.0) as w:
            for step in w.steps(start=start, total=stop):
                w.set("loss", float(step))
        ckpt.write_text(json.dumps({"next": stop}))

    variant = runstate.Variant(
        "exp", chunked, {"kwargs": {"run_id": "exp", "ckpt_dir": str(tmp_path)}}
    )
    launcher.open_channel("exp").send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="obs"
    )
    producer = launch_producer(launcher, variant)
    series = ensure(producer, "loss", up_to=7)            # 0..2, re-drive 3..5, re-drive 6
    assert [b["step"] for b in series] == [0, 1, 2, 3, 4, 5, 6]
    assert len(launcher.open_channel("exp").read(topics=["launcher.launched"])) >= 3


def test_ensure_surfaces_a_die_before_attach_without_hanging():
    # The worker raises BEFORE constructing Worker -> no lifecycle.started, only
    # the launcher's terminated. ensure must surface it (via the new-terminated
    # gate), not hang waiting for a started that never comes.
    launcher = runstate.ThreadLauncher()

    def die_early(channel, *, up_to):
        raise RuntimeError("died before attaching")

    variant = runstate.Variant("exp", die_early, {"kwargs": {}})
    producer = launch_producer(launcher, variant)
    with pytest.raises(RuntimeError, match="failed"):
        ensure(producer, "loss", up_to=5)


def test_ensure_redrives_when_extend_noops_onto_a_live_episode(tmp_path):
    # A foreign episode is already LIVE when ensure runs, so extend no-ops
    # (drove=False). When that episode then stops BELOW target, ensure must
    # re-drive -- NOT raise "no progress" (the bug the drove-gate fixes).
    launcher = runstate.ThreadLauncher()
    rid = "exp"
    seed = launcher.open_channel(rid)
    # a live foreign episode (started by our pid -> resolve() alive, no stopped),
    # having emitted loss 0,1 and beaconed step 1
    seed.send({"handle": local_handle(), "hostname": None, "attached_at": 0.0},
              topic="lifecycle.started")
    seed.send({"value": 0.0, "step": 0, "t": 0.0}, topic="value", name="loss", request_id="obs")
    seed.send({"value": 1.0, "step": 1, "t": 0.0}, topic="value", name="loss", request_id="obs")
    seed.send({"step": 1, "consumed_seq": 0}, topic="lifecycle.heartbeat")
    # pre-stage the subscription so the re-drive episode emits; the checkpoint
    # says the foreign episode reached step 2 (the re-drive resumes there)
    seed.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="obs")
    (tmp_path / f"{rid}.json").write_text(json.dumps({"next": 2}))

    variant = runstate.Variant(
        rid, _cell, {"kwargs": {"run_id": rid, "ckpt_dir": str(tmp_path)}}
    )
    producer = launch_producer(launcher, variant)

    # deterministically end the foreign episode BELOW target on the first sleep
    ended = {"done": False}

    def driver_sleep(_):
        if not ended["done"]:
            ended["done"] = True
            launcher.open_channel(rid).send(
                {"reason": "completed", "error": None, "final_step": 1},
                topic="lifecycle.stopped",
            )

    series = ensure(producer, "loss", up_to=4, sleep=driver_sleep)
    assert [b["step"] for b in series] == [0, 1, 2, 3]   # foreign 0,1 + re-driven 2,3 = one series


def test_public_exports_present():
    assert {"history", "ensure", "launch_producer", "relaunch_if_needed"} <= set(runstate.__all__)
    assert all(hasattr(runstate, n)
               for n in ("history", "ensure", "launch_producer", "relaunch_if_needed"))
