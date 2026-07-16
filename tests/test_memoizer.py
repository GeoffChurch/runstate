import json
import pytest
from pathlib import Path
import runstate
from runstate.memoizer import (NoProgressError, RunFailedError, ensure,
                               history, launch_producer)
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


def test_history_collapses_re_emission_taking_the_latest(open_channel):
    # A resumed episode re-emits the checkpoint overlap; history collapses by step,
    # taking the latest (highest-seq) record -- the as-resumed / continuing branch.
    # (docs/backlog/value-plane-divergence-resolution.md)
    ch = open_channel()
    ch.send({"value": 1.0, "step": 0, "t": 0.0}, topic="value", name="loss")
    ch.send({"value": 2.0, "step": 1, "t": 0.0}, topic="value", name="loss")
    ch.send({"value": 1.0, "step": 0, "t": 9.0}, topic="value", name="loss")   # identical re-emit -> collapses
    assert [b["step"] for b in history(open_channel(), "loss", {"every": {"step": 1}})] == [0, 1]
    ch.send({"value": 99.0, "step": 1, "t": 9.0}, topic="value", name="loss")  # DIVERGENT re-emit (a resume)
    got = history(open_channel(), "loss", {"every": {"step": 1}})
    assert [(b["step"], b["value"]) for b in got] == [(0, 1.0), (1, 99.0)]      # take-the-latest by seq


def test_history_time_schedule_is_run_relative_to_the_run_epoch(open_channel):
    ch = open_channel()
    ch.send({"handle": "local://h/1", "t": 1000.0},
            topic="lifecycle.started")                          # run epoch = 1000.0
    for step in range(6):
        ch.send({"value": float(step), "step": step, "t": 1000.0 + step},
                topic="value", name="loss")                     # absolute t
    # "every 2 seconds" run-relative: t-epoch in {0,2,4} -> steps 0,2,4
    got = history(open_channel(), "loss", {"every": {"time_seconds": 2}})
    assert [b["step"] for b in got] == [0, 2, 4]


def test_history_skips_nonconforming_value_records(open_channel):
    # The substrate admits foreign bodies on any topic; junk on `value` with a
    # matching name is not a point in the series -- skipped, as the observables'
    # measurement folds skip it (the tolerance split).
    ch = open_channel()
    ch.send({"value": 0.0, "step": 0, "t": 0.0}, topic="value", name="loss")
    ch.send({"note": "junk"}, topic="value", name="loss")                         # no value/step/t
    ch.send({"value": 1.0, "step": 1}, topic="value", name="loss")                # missing t
    ch.send({"value": 2.0, "step": "two", "t": 0.0}, topic="value", name="loss")  # wrong-typed step
    ch.send({"value": 3.0, "step": True, "t": 0.0}, topic="value", name="loss")   # bool is not a step
    ch.send({"value": 4.0, "step": 2, "t": True}, topic="value", name="loss")     # bool is not a t
    ch.send({"value": 5.0, "step": 2, "t": 0.0}, topic="value", name="loss")
    got = history(open_channel(), "loss", {"every": {"step": 1}})
    assert [(b["step"], b["value"]) for b in got] == [(0, 0.0), (2, 5.0)]


def test_history_conforming_stepless_point_still_raises(open_channel):
    # A CONFORMING point with `step` present-and-null is a real domain error
    # (history is a stepped-trajectory reader), never junk to skip.
    ch = open_channel()
    ch.send({"value": 1.0, "step": None, "t": 0.0}, topic="value", name="loss")
    with pytest.raises(ValueError, match="stepped emission"):
        history(open_channel(), "loss", {"every": {"step": 1}})


def test_history_time_schedule_requires_a_run_epoch(open_channel):
    # No epoch -> no run-relative clock to anchor a time-referencing replay:
    # raise, never anchor at 0.0 (absolute value.t would satisfy untils
    # instantly). Step-only schedules never touch the epoch.
    ch = open_channel()
    ch.send({"value": 0.0, "step": 0, "t": 1000.0}, topic="value", name="loss")
    with pytest.raises(ValueError, match="run epoch"):        # no started record
        history(open_channel(), "loss", {"every": {"time_seconds": 2}})
    ch.send({"handle": "local://h/1", "t": None},
            topic="lifecycle.started")
    with pytest.raises(ValueError, match="run epoch"):        # null t (non-numeric -> no epoch)
        history(open_channel(), "loss", {"every": {"time_seconds": 2}})
    got = history(open_channel(), "loss", {"every": {"step": 1}})
    assert [b["step"] for b in got] == [0]


def test_history_null_t_points_are_inert_for_time_conditions(open_channel):
    # t=None -> the run-relative clock cannot advance at that point: time-keyed
    # conditions see it at the epoch (inert); step conditions are unaffected.
    ch = open_channel()
    ch.send({"handle": "local://h/1", "t": 1000.0},
            topic="lifecycle.started")
    ch.send({"value": 0.0, "step": 0, "t": 1000.0}, topic="value", name="loss")
    ch.send({"value": 1.0, "step": 1, "t": None}, topic="value", name="loss")
    ch.send({"value": 2.0, "step": 2, "t": 1002.0}, topic="value", name="loss")
    got = history(open_channel(), "loss", {"every": {"time_seconds": 2}})
    assert [b["step"] for b in got] == [0, 2]


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
    producer.extend({"step": 3})                             # launches; injects up_to=3
    for _ in range(400):                                    # wait for the episode to finish
        if launcher.open_channel("exp").latest("lifecycle.stopped") is not None:
            break
        _t.sleep(0.005)
    assert seen["up_to"] == 3                                # target injected into worker kwargs


def test_relaunch_if_needed_noops_when_a_live_episode_exists():
    launcher = runstate.ThreadLauncher()
    ch = launcher.open_channel("r")
    # fake a live episode: a started by OUR pid (resolve() -> alive), no stopped
    ch.send({"handle": local_handle(), "t": 0.0},
            topic="lifecycle.started")
    h = relaunch_if_needed(launcher, "r", lambda channel, **_: None, kwargs={})
    assert h is None
    assert launcher.open_channel("r").read(topics=["launcher.launched"]) == []  # no spawn


def _cell(channel, *, run_id, up_to, ckpt_dir):
    """A resumable worker: reads its run_id-keyed checkpoint, continues the
    run-absolute step, checkpoints the new frontier. Subscription-gated.

    Resumable by default — falls off the ``with`` without claiming ``completed``,
    so the default ``preempted`` applies. This worker never self-declares
    convergence; ensure can re-drive it with a higher target."""
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
    series = ensure(producer, "loss", until={"step": 5})
    assert [b["step"] for b in series] == [0, 1, 2, 3, 4]
    launched = len(launcher.open_channel("exp").read(topics=["launcher.launched"]))
    series2 = ensure(producer, "loss", until={"step": 5})     # hit: no relaunch
    assert [b["step"] for b in series2] == [0, 1, 2, 3, 4]
    assert len(launcher.open_channel("exp").read(topics=["launcher.launched"])) == launched


def test_ensure_extends_partial_prefix_into_one_series(tmp_path):
    launcher = runstate.ThreadLauncher()
    producer = _producer(launcher, tmp_path)
    ensure(producer, "loss", until={"step": 3})               # ep1: 0,1,2
    series = ensure(producer, "loss", until={"step": 6})      # ep2 resumes: 3,4,5
    assert [b["step"] for b in series] == [0, 1, 2, 3, 4, 5]  # one continuous series


def test_ensure_surfaces_a_failure_outcome(tmp_path):
    launcher = runstate.ThreadLauncher()

    def crash(channel, *, up_to):
        with runstate.Worker(channel, now=lambda: 0.0) as w:
            for step in w.steps(total=up_to):
                raise RuntimeError("boom")

    variant = runstate.Variant("exp", crash, {"kwargs": {}})
    producer = launch_producer(launcher, variant)
    with pytest.raises(RunFailedError, match="failed") as ei:
        ensure(producer, "loss", until={"step": 5})
    assert ei.value.result.outcome in runstate.Outcome.failures()   # the verdict, at raise time
    assert ei.value.run_id == "exp"


def test_ensure_raises_when_run_makes_no_progress(tmp_path):
    launcher = runstate.ThreadLauncher()

    def stuck(channel, *, up_to):          # ignores up_to; always 2 steps, no ckpt
        with runstate.Worker(channel, now=lambda: 0.0) as w:
            for step in w.steps(total=2):
                w.set("loss", float(step))
            # Falls off without claiming ``completed`` -> default ``preempted``.
            # This keeps ensure looping so the no-progress guard fires.

    variant = runstate.Variant("exp", stuck, {"kwargs": {}})
    launcher.open_channel("exp").send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="obs"
    )
    producer = launch_producer(launcher, variant)
    with pytest.raises(NoProgressError, match="progress") as ei:
        ensure(producer, "loss", until={"step": 5})
    assert ei.value.progress == 1 and ei.value.until == {"step": 5}   # the diagnostics, as data


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
            if stop >= up_to:
                w.stopped(completed=True)   # reached the full target -> intrinsic done
            # else: fall off -> default preempted (more to do)
        ckpt.write_text(json.dumps({"next": stop}))

    variant = runstate.Variant(
        "exp", chunked, {"kwargs": {"run_id": "exp", "ckpt_dir": str(tmp_path)}}
    )
    launcher.open_channel("exp").send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="obs"
    )
    producer = launch_producer(launcher, variant)
    series = ensure(producer, "loss", until={"step": 7})  # 0..2, re-drive 3..5, re-drive 6
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
    with pytest.raises(RunFailedError, match="failed"):
        ensure(producer, "loss", until={"step": 5})


def test_ensure_redrives_when_extend_noops_onto_a_live_episode(tmp_path):
    # A foreign episode is already LIVE when ensure runs, so the gate hands
    # back its foreign handle. When that episode then stops BELOW target
    # (preempted, no progress during our watch), ensure must re-drive -- NOT
    # raise "no progress" (foreign handles are exempt from the guard).
    launcher = runstate.ThreadLauncher()
    rid = "exp"
    seed = launcher.open_channel(rid)
    # a live foreign episode (started by our pid -> resolve() alive, no stopped),
    # having emitted loss 0,1 and beaconed step 1
    seed.send({"handle": local_handle(), "t": 0.0},
              topic="lifecycle.started")
    seed.send({"value": 0.0, "step": 0, "t": 0.0}, topic="value", name="loss", request_id="obs")
    seed.send({"value": 1.0, "step": 1, "t": 0.0}, topic="value", name="loss", request_id="obs")
    seed.send({"step": 1, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
    # pre-stage the subscription so the re-drive episode emits; the checkpoint
    # says the foreign episode reached step 2 (the re-drive resumes there)
    seed.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="obs")
    (tmp_path / f"{rid}.json").write_text(json.dumps({"next": 2}))

    variant = runstate.Variant(
        rid, _cell, {"kwargs": {"run_id": rid, "ckpt_dir": str(tmp_path)}}
    )
    producer = launch_producer(launcher, variant)

    # deterministically end the foreign episode BELOW target on the first sleep.
    # Use ``preempted`` (not ``completed``) so ensure re-drives it -- a
    # ``completed`` stop would cause ensure to return early under the new semantics.
    ended = {"done": False}

    def driver_sleep(_):
        if not ended["done"]:
            ended["done"] = True
            launcher.open_channel(rid).send(
                {"completed": False, "error": None, "final_step": 1, "t": 0.0},
                topic="lifecycle.stopped",
            )

    series = ensure(producer, "loss", until={"step": 4}, sleep=driver_sleep)
    assert [b["step"] for b in series] == [0, 1, 2, 3]   # foreign 0,1 + re-driven 2,3 = one series


def test_public_exports_present():
    assert {"history", "ensure", "launch_producer", "relaunch_if_needed",
            "foreign_episode"} <= set(runstate.__all__)
    assert all(hasattr(runstate, n)
               for n in ("history", "ensure", "launch_producer",
                         "relaunch_if_needed", "foreign_episode"))


# ---------------------------------------------------------------------------
# Synthetic-channel tests for the completed-short-of-up_to behaviour
# ---------------------------------------------------------------------------

class _FakeProducer:
    """A minimal duck-typed producer backed by a pre-seeded MemoryChannel.

    `extend` drives synchronously (the side effect appends the episode) and
    returns an already-dead own-spawn handle -- the seam contract requires a
    liveness handle, never None (specs/store.md Recipe 2). It counts how many
    times it was called; the channel is shared so the caller can inspect /
    mutate it after construction.
    """

    def __init__(self, channel, extend_side_effect=None):
        self._channel = channel
        self._extend_side_effect = extend_side_effect
        self.run_id = "fake-run"
        self.extend_calls = 0

    @property
    def channel(self):
        return self._channel

    def extend(self, until):
        self.extend_calls += 1
        if self._extend_side_effect is not None:
            self._extend_side_effect(self._channel, until)
        return _DeadHandle()   # synchronous own drive, already finished


def _seed_episode(ch, *, heartbeat_step, completed: bool, value_steps=None):
    """Write a completed single-episode lifecycle into *ch* (no live episode after)."""
    from runstate.vocabulary.handle import local_handle
    ch.send(
        {"handle": local_handle(), "t": 0.0},
        topic="lifecycle.started",
    )
    ch.send({"step": heartbeat_step, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
    if value_steps is not None:
        for s in value_steps:
            ch.send({"value": float(s), "step": s, "t": 0.0},
                    topic="value", name="loss")
    ch.send(
        {"completed": completed, "error": None, "final_step": heartbeat_step, "t": 0.0},
        topic="lifecycle.stopped",
    )


def test_ensure_completed_short_of_up_to_returns_without_redriving():
    """Test 1: a channel with completed short of up_to -> ensure returns; extend NOT called."""
    from runstate.channel.memory import MemoryChannel

    ch = MemoryChannel()
    K = 3
    _seed_episode(ch, heartbeat_step=K, completed=True,
                  value_steps=list(range(K + 1)))

    producer = _FakeProducer(ch)
    # until={"step": K+5} means the target is far beyond what the worker produced
    series = ensure(producer, "loss", until={"step": K + 5})

    # ensure returns the available trajectory (steps 0..K)
    assert [b["step"] for b in series] == list(range(K + 1))
    # extend was NEVER called — the read-first completed-check fired
    assert producer.extend_calls == 0


def test_ensure_preempted_redrives_then_stops_on_completion():
    """Test 2: preempted short -> ensure re-drives once; on completion it stops (extend called 1x)."""
    from runstate.channel.memory import MemoryChannel

    ch = MemoryChannel()
    K = 2    # first episode stops preempted at step K
    M = 4    # second episode stops completed at step M (still < up_to-1)
    up_to = 20

    # Seed the first (preempted) episode
    _seed_episode(ch, heartbeat_step=K, completed=False,
                  value_steps=list(range(K + 1)))

    def _extend_side_effect(channel, target):
        """On the producer's first extend call, append a second episode that completes."""
        from runstate.vocabulary.handle import local_handle
        channel.send(
            {"handle": local_handle(), "t": 1.0},
            topic="lifecycle.started",
        )
        channel.send({"step": M, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
        for s in range(K + 1, M + 1):
            channel.send({"value": float(s), "step": s, "t": 1.0},
                         topic="value", name="loss")
        channel.send(
            {"completed": True, "error": None, "final_step": M, "t": 0.0},
            topic="lifecycle.stopped",
        )

    producer = _FakeProducer(ch, extend_side_effect=_extend_side_effect)
    series = ensure(producer, "loss", until={"step": up_to})

    # ensure returns steps 0..M (both episodes combined)
    assert [b["step"] for b in series] == list(range(M + 1))
    # extend was called EXACTLY ONCE (re-drove the preempted, then saw completed)
    assert producer.extend_calls == 1


def test_ensure_preempted_that_reaches_up_to_uses_progress_hit(tmp_path):
    """Test 3 (sanity): preempted that reaches up_to -> normal progress hit; ensure returns."""
    launcher = runstate.ThreadLauncher()
    producer = _producer(launcher, tmp_path)
    series = ensure(producer, "loss", until={"step": 5})
    # The worker stops preempted (no self-completion claim) but reaches step 4 (up_to-1)
    assert [b["step"] for b in series] == [0, 1, 2, 3, 4]
    # The channel has a stopped record
    stopped = launcher.open_channel("exp").latest("lifecycle.stopped")
    assert stopped is not None
    # ensure returned via the progress hit (existing behavior unbroken)


def test_ensure_killed_resumes_on_caller_re_call_take_the_latest():
    # ensure FAILS FAST on a death (the retry decision is the CALLER's, not an
    # in-ensure policy). The caller re-calls ensure to resume: read-first sees
    # progress-but-not-done, relaunches from the checkpoint, and G1 take-the-latest
    # absorbs the resumed overlap. This is the supported killed-redrive pattern.
    from runstate.channel.memory import MemoryChannel
    from runstate.vocabulary.handle import local_handle

    ch = MemoryChannel()
    calls = {"n": 0}

    def episodes(channel, target):
        calls["n"] += 1
        launch = f"L{calls['n']}"                # each episode answers its own launch
        channel.send({"handle": local_handle(), "t": float(calls["n"])},
                     topic="lifecycle.started", request_id=launch)
        if calls["n"] == 1:                      # progress 0..2, then KILLED (external signal)
            channel.send({"step": 2, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
            for s in range(3):
                channel.send({"value": float(s), "step": s, "t": 0.0}, topic="value", name="loss")
            channel.send({"reason": "killed", "exit_code": None, "signal": 9, "t": 0.0},
                         topic="launcher.terminated", request_id=launch)
        else:                                    # resume behind frontier: re-emit step 2 divergently, then 3..5, complete
            channel.send({"step": 5, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
            for s, v in [(2, 2.5), (3, 3.0), (4, 4.0), (5, 5.0)]:
                channel.send({"value": v, "step": s, "t": 1.0}, topic="value", name="loss")
            channel.send({"completed": True, "error": None, "final_step": 5, "t": 0.0},
                         topic="lifecycle.stopped")

    producer = _FakeProducer(ch, extend_side_effect=episodes)

    # First call: ensure drives, hits the kill, and fails fast (no auto-redrive).
    with pytest.raises(RunFailedError, match="failed"):
        ensure(producer, "loss", until={"step": 10})
    assert producer.extend_calls == 1

    # The caller decides to retry -> a re-call resumes from the checkpoint.
    series = ensure(producer, "loss", until={"step": 10})
    assert [(b["step"], b["value"]) for b in series] == \
        [(0, 0.0), (1, 1.0), (2, 2.5), (3, 3.0), (4, 4.0), (5, 5.0)]   # step 2 = take-the-latest
    assert producer.extend_calls == 2


def test_launch_producer_rejects_non_step_condition():
    launcher = runstate.ThreadLauncher()
    variant = runstate.Variant("exp", lambda channel, *, up_to: None, {"kwargs": {}})
    producer = launch_producer(launcher, variant)
    for bad in ({"time_seconds": 5}, {"count": 3},
                {"all": [{"step": 1}, {"time_seconds": 2}]}):
        with pytest.raises(ValueError, match="translates only"):  # matches the real message
            producer.extend(bad)


# ---------------------------------------------------------------------------
# Task 2: Time axis — poll-clock satisfaction + axis-aware no-progress guard
# ---------------------------------------------------------------------------

class _DeadHandle:
    """A LaunchHandle that reports its episode already finished."""
    def is_alive(self): return False
    def wait(self): pass

class _RampClock:
    """Monotone poll-clock: +`step` per call, so _elapsed crosses any budget."""
    def __init__(self, step=1.0): self.t = -step; self.step = step
    def __call__(self): self.t += self.step; return self.t

class _ZeroStepTimeProducer:
    """Each extend drives a chunk that makes NO step progress and ends
    `preempted` (a live episode we drove -> handle is not None)."""
    run_id = "fake"
    def __init__(self, channel): self._c = channel; self.calls = 0
    @property
    def channel(self): return self._c
    def extend(self, until):
        self.calls += 1
        self._c.send({"completed": False, "error": None, "final_step": 0, "t": 0.0},
                     topic="lifecycle.stopped")
        return _DeadHandle()

def test_ensure_time_milestone_does_not_false_raise_on_zero_step_progress():
    """A {time_seconds} chunk that advances 0 steps while the clock advances must
    NOT trip the no-progress guard (critical-c). With the OLD step-only guard the
    first drive raises (progress 0 <= before 0); with the axis-aware guard the
    ramp clock eventually satisfies and ensure returns."""
    from runstate.channel.memory import MemoryChannel
    from runstate.vocabulary.handle import local_handle

    ch = MemoryChannel()
    ch.send({"handle": local_handle(), "t": 0.0},
            topic="lifecycle.started")            # epoch 0.0
    ch.send({"step": 0, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")  # 0 steps
    ch.send({"value": 0.0, "step": 0, "t": 0.0}, topic="value", name="loss")

    series = ensure(_ZeroStepTimeProducer(ch), "loss", until={"time_seconds": 5},
                    clock=_RampClock(), poll_interval=0)
    assert [b["step"] for b in series] == [0]      # returned, did not raise


def test_ensure_time_milestone_satisfies_via_poll_clock_even_when_value_sparse():
    """Sparse `value` (only step 0 emitted, value.t frozen at 0) must not livelock a
    {time_seconds} milestone: satisfaction reads the poll-clock, not value.t."""
    from runstate.channel.memory import MemoryChannel
    from runstate.vocabulary.handle import local_handle

    ch = MemoryChannel()
    ch.send({"handle": local_handle(), "t": 0.0},
            topic="lifecycle.started")
    ch.send({"value": 0.0, "step": 0, "t": 0.0}, topic="value", name="loss")  # value.t frozen at 0
    ch.send({"step": 0, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")

    series = ensure(_ZeroStepTimeProducer(ch), "loss", until={"time_seconds": 5},
                    clock=_RampClock(), poll_interval=0)
    assert [b["step"] for b in series] == [0]   # crossed the budget on the clock, not value.t


# ---------------------------------------------------------------------------
# Task 3: Reject the `count` drive-axis at entry
# ---------------------------------------------------------------------------

def test_ensure_rejects_count_drive_condition():
    from runstate.channel.memory import MemoryChannel
    producer = _FakeProducer(MemoryChannel())
    for bad in ({"count": 3}, {"any": [{"step": 5}, {"count": 3}]}):
        with pytest.raises(ValueError, match="count"):
            ensure(producer, "loss", until=bad)


# ---------------------------------------------------------------------------
# Task 4: Compound `all` + the `completed`/`preempted` discipline on the time axis
# ---------------------------------------------------------------------------

class _StepThenWaitProducer:
    """First extend drives to step 2 (meets a {step:3} window); later extends make
    NO new step progress. With the axis-aware guard the step-met/time-pending
    compound must NOT false-raise -- time (the ramp clock) finishes it. (The old
    step-only guard WOULD raise on the 2nd extend: progress 2 <= before 2.)"""
    run_id = "fake"
    def __init__(self, channel): self._c = channel; self.calls = 0
    @property
    def channel(self): return self._c
    def extend(self, until):
        from runstate.vocabulary.handle import local_handle
        self.calls += 1
        if self.calls == 1:
            self._c.send({"handle": local_handle(), "t": 0.0},
                         topic="lifecycle.started")
            for s in range(3):
                self._c.send({"value": float(s), "step": s, "t": 0.0},
                             topic="value", name="loss")
            self._c.send({"step": 2, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
        self._c.send({"completed": False, "error": None, "final_step": 2, "t": 0.0},
                     topic="lifecycle.stopped")
        return _DeadHandle()

def test_ensure_compound_all_step_met_time_pending_does_not_false_raise():
    from runstate.channel.memory import MemoryChannel
    p = _StepThenWaitProducer(MemoryChannel())
    series = ensure(p, "loss", until={"all": [{"step": 3}, {"time_seconds": 5}]},
                    clock=_RampClock(), poll_interval=0)
    assert [b["step"] for b in series] == [0, 1, 2]   # window [0,3); both bounds met by return
    assert p.calls >= 2                               # re-drove after step met, on time -- no raise


class _TimeChunkProducer:
    """Each extend drives a one-step chunk stopping with `completed` below the time
    budget. preempted (completed=False) -> ensure re-drives until the ramp clock
    reaches the budget, accumulating steps; completed (completed=True) -> ensure
    stops after the first chunk."""
    run_id = "fake"
    def __init__(self, channel, completed: bool): self._c = channel; self._completed = completed; self.calls = 0; self._s = 0
    @property
    def channel(self): return self._c
    def extend(self, until):
        from runstate.vocabulary.handle import local_handle
        if self.calls == 0:
            self._c.send({"handle": local_handle(), "t": 0.0},
                         topic="lifecycle.started")
        self.calls += 1
        self._c.send({"value": float(self._s), "step": self._s, "t": 0.0}, topic="value", name="loss")
        self._c.send({"step": self._s, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
        self._c.send({"completed": self._completed, "error": None, "final_step": self._s, "t": 0.0}, topic="lifecycle.stopped")
        self._s += 1
        return _DeadHandle()

def test_ensure_time_budget_preempted_accumulates_across_chunks():
    from runstate.channel.memory import MemoryChannel
    p = _TimeChunkProducer(MemoryChannel(), completed=False)
    series = ensure(p, "loss", until={"time_seconds": 5}, clock=_RampClock(), poll_interval=0)
    assert p.calls >= 2                                        # re-drove across timed chunks
    assert [b["step"] for b in series] == list(range(p.calls)) # one continuous accumulated series

def test_ensure_time_budget_completed_truncates_after_first_chunk():
    # DISCIPLINE: a time-budgeted RESUMABLE worker MUST emit `preempted`, never
    # `completed` -- a per-chunk `completed` makes ensure stop after one chunk,
    # silently truncating the wall-clock budget.
    from runstate.channel.memory import MemoryChannel
    p = _TimeChunkProducer(MemoryChannel(), completed=True)
    series = ensure(p, "loss", until={"time_seconds": 5}, clock=_RampClock(), poll_interval=0)
    assert p.calls == 1 and [b["step"] for b in series] == [0]   # completed -> stopped at chunk 1


# ----- derived runs: the dissolution pin (specs/derived-runs.md) -----


def _derived_worker(channel, *, up_to=None, **_):
    """The derived-run convention: one step, hand-emit the bundle
    (emit-only-missing), claim completed. `set()`+tick would emit nothing --
    ensure never subscribes."""
    from dataclasses import asdict
    with runstate.Worker(channel, now=lambda: 0.0) as w:
        for _step in w.steps(total=1):
            existing = {e.name for e in channel.read(topics=["value"])}
            for k, v in {"pair_metrics": 0.42, "hubness": 7.0}.items():
                if k not in existing:
                    channel.send(asdict(runstate.Value(value=v, step=0, t=0.0)),
                                 topic="value", name=k)
        w.stopped(completed=True)


def test_derived_run_dissolution_pin(tmp_path):
    """specs/derived-runs.md: compute-on-demand needs NO new library surface --
    a one-step, hand-emitting, completed-claiming worker behind the EXISTING
    ensure is the whole "function producer". If this test cannot pass without
    new library code, the dissolution finding is refuted."""
    launcher = runstate.ThreadLauncher()
    rid = "analysis-abc123"      # = run_id({analyzed, inputs, params, code})
    variant = runstate.Variant(rid, _derived_worker, {"kwargs": {}})
    producer = launch_producer(launcher, variant)

    series = ensure(producer, "pair_metrics", until={"step": 1})
    assert [(b["step"], b["value"]) for b in series] == [(0, 0.42)]   # non-empty
    ch = launcher.open_channel(rid)
    assert len(ch.read(topics=["lifecycle.started"])) == 1

    series2 = ensure(producer, "pair_metrics", until={"step": 1})     # cache hit
    assert series2 == series
    assert len(ch.read(topics=["lifecycle.started"])) == 1            # no relaunch
    # the whole bundle is on the log for any value_series consumer
    assert set(runstate.value_series(ch)) == {"pair_metrics", "hubness"}


# ----- the Store: the dissolution pins (specs/store.md) -----


def _pin_producer(launcher, ckpt_dir, rid, *, stage_subscription=True):
    """A `_cell` producer for the store pins; the subscription is staged at
    most once (two drivers share ONE log -- double-staging would
    double-register the demand)."""
    if stage_subscription:
        launcher.open_channel(rid).send(
            {"every": {"step": 1}}, topic="control.subscribe",
            name="loss", request_id="obs")
    variant = runstate.Variant(
        rid, _cell, {"kwargs": {"run_id": rid, "ckpt_dir": str(ckpt_dir)}}
    )
    return launch_producer(launcher, variant)


def test_store_pin_reuse_is_extend_across_drivers(tmp_path):
    """specs/store.md pin 1: two independent drivers ("experiments") demanding
    one rid converge on ONE content-addressed home -- A computes a preempted
    prefix, B extends the SAME log (no second channel file, no recompute of
    the prefix). Must pass on shipped machinery alone; otherwise the
    dissolution is refuted."""
    rid = "abc123"
    home = tmp_path / "runs" / rid[:2] / rid              # Recipe-1 layout
    home.mkdir(parents=True)
    ckpts = tmp_path / "ckpts"
    ckpts.mkdir()
    driver_a = runstate.ThreadLauncher(root=str(home), backend="sqlite")
    driver_b = runstate.ThreadLauncher(root=str(home), backend="sqlite")
    prod_a = _pin_producer(driver_a, ckpts, rid)
    prod_b = _pin_producer(driver_b, ckpts, rid, stage_subscription=False)

    series_a = ensure(prod_a, "loss", until={"step": 3})
    assert [b["step"] for b in series_a] == [0, 1, 2]
    series_b = ensure(prod_b, "loss", until={"step": 8})  # reuse IS extend
    assert [b["step"] for b in series_b] == list(range(8))

    dbs = sorted((tmp_path / "runs").rglob("*.db"))
    assert dbs == [home / f"{rid}.db"]                    # one home, one log
    episodes = driver_b.open_channel(rid).read(topics=["lifecycle.started"])
    assert len(episodes) == 2                             # A's prefix + B's extension


class _ForeignWait:
    """specs/store.md Recipe 2, inlined test-locally (the pin-2a counterfactual:
    the gate's handle shape is buildable on shipped machinery alone).
    `is_alive()` re-reads the log every poll; `wait()` is a no-op."""
    def __init__(self, channel):
        self._channel = channel
    def is_alive(self):
        return runstate.live_episode(self._channel) is not None
    def wait(self):
        pass


def test_store_pin_latecomer_waits_on_live_foreign_episode():
    """specs/store.md pin 2a: while a foreign episode is LIVE, a gated
    producer's ensure poll-waits -- zero launches -- and returns the satisfied
    history once the winner delivers."""
    launcher = runstate.ThreadLauncher()
    rid = "exp"
    ch = launcher.open_channel(rid)
    ch.send({"handle": local_handle(), "t": 0.0},
            topic="lifecycle.started")            # the live foreign winner (our pid)

    class _Gated:                                 # the Recipe-2 gate, latecomer side
        run_id = rid
        channel = ch
        def extend(self, until):
            assert runstate.live_episode(ch) is not None   # the pin gates, never launches
            return _ForeignWait(ch)

    delivered = {"n": 0}

    def winner_delivers(_):                       # the foreign winner, via the poll hook
        s = delivered["n"]
        if s < 5:
            ch.send({"value": float(s), "step": s, "t": 0.0},
                    topic="value", name="loss")
            ch.send({"step": s, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
            delivered["n"] += 1

    series = ensure(_Gated(), "loss", until={"step": 5}, sleep=winner_delivers)
    assert [b["step"] for b in series] == [0, 1, 2, 3, 4]
    assert ch.read(topics=["launcher.launched"]) == []     # latecomer launched nothing
    assert delivered["n"] == 5                             # it genuinely waited


def test_store_pin_latecomer_recovers_when_foreign_winner_dies_recordless(tmp_path):
    """specs/store.md pin 2b: a foreign winner that dies RECORDLESS mid-wait
    (no stopped, no terminated -- a SIGKILLed, never-reaped runner) must not
    strand the gated latecomer: `is_alive()` re-reads the log, the wait
    breaks, and the next extend RE-DRIVES (relaunch_if_needed sees no live
    episode and launches the recovery episode -- the lazy-launch re-wake
    posture). The None-gate polls forever here; a hang-guard sleep converts
    a hang into a loud failure."""
    import socket
    import time
    launcher = runstate.ThreadLauncher()
    rid = "exp"
    ch = launcher.open_channel(rid)
    ch.send({"every": {"step": 1}}, topic="control.subscribe",
            name="loss", request_id="obs")
    ch.send({"handle": local_handle(), "t": 0.0},
            topic="lifecycle.started")            # the live foreign winner...

    producer = _pin_producer(launcher, tmp_path, rid, stage_subscription=False)
    calls = {"n": 0}
    host = socket.gethostname()

    def hang_guard(_):
        calls["n"] += 1
        if calls["n"] == 1:                       # ...replaced by a claim that died recordless
            ch.send({"handle": f"local://{host}/2147483646",
                     "t": 0.0}, topic="lifecycle.started")
        time.sleep(0.001)                         # real yield: the recovery runs in a thread
        if calls["n"] > 200:
            raise AssertionError(
                "ensure is hanging: the gate stranded the latecomer on a "
                "recordless-dead foreign episode")

    series = ensure(producer, "loss", until={"step": 4}, sleep=hang_guard)
    assert [b["step"] for b in series] == [0, 1, 2, 3]
    launched = ch.read(topics=["launcher.launched"])
    assert len(launched) == 1                     # exactly one recovery spawn


def test_foreign_episode_helper_tracks_live_episode():
    """specs/store.md pin 3 (the helper): `foreign_episode(channel)` is the
    public one-copy of the gate's foreign half (the F7 doctrine) -- is_alive()
    re-reads live_episode on every call; wait() is a no-op."""
    from runstate import foreign_episode
    from runstate.channel.memory import MemoryChannel

    ch = MemoryChannel()
    handle = foreign_episode(ch)
    assert handle.is_alive() is False             # empty log: no episode
    ch.send({"handle": local_handle(), "t": 0.0},
            topic="lifecycle.started")
    assert handle.is_alive() is True              # claim landed: live
    ch.send({"completed": False, "error": None, "final_step": 0, "t": 0.0},
            topic="lifecycle.stopped")
    assert handle.is_alive() is False             # episode over
    assert handle.wait() is None                  # nothing to reap


def test_ensure_collision_skips_no_progress_raise_when_foreign_episode_lives(open_channel):
    # the claim-window collision (red-team P1): the own spawn died recordless
    # with zero progress, but a LIVE foreign episode holds the claim -- the
    # run isn't "stuck", someone else owns it. The claim-aware guard skips the
    # raise; ensure re-enters and waits on the winner. The genuinely-stuck
    # case (no live episode) still raises -- pinned by the no-progress tests.
    ch = open_channel()
    calls = []

    class _DeadHandle:
        def is_alive(self):
            return False

        def wait(self):
            return None

    class _Collider:
        run_id = "collide"

        def __init__(self, channel):
            self.channel = channel

        def extend(self, until):
            calls.append(len(calls))
            if len(calls) == 1:
                # the loser's spawn: dies recordless; the winner's claim lives
                self.channel.send(
                    {"handle": local_handle(), "t": 0.0},
                    topic="lifecycle.started")
            else:
                # second pass: the winner delivers the window
                for s in range(3):
                    self.channel.send({"value": float(s), "step": s, "t": None},
                                      topic="value", name="loss")
                self.channel.send({"step": 2, "consumed_seq": 0, "t": 0.0},
                                  topic="lifecycle.heartbeat")
            return _DeadHandle()

    series = ensure(_Collider(ch), "loss", until={"step": 3})
    assert [b["step"] for b in series] == [0, 1, 2]
    assert len(calls) == 2                        # re-entered, never raised


@pytest.mark.parametrize("junk", [{"j": 1}, "garbage", "3.5", True, [1]])
def test_history_junk_epoch_reads_as_no_epoch(open_channel, junk):
    # a junk-typed t earns no epoch (the measurement rule):
    # time-referencing replay raises the typed complaint -- never an untyped
    # float() TypeError -- and step-only replay is unaffected.
    ch = open_channel()
    ch.send({"handle": "local://h/1", "t": junk},
            topic="lifecycle.started")
    ch.send({"value": 1.0, "step": 0, "t": 5.0}, topic="value", name="loss")
    with pytest.raises(ValueError, match="epoch"):
        history(open_channel(), "loss", {"every": {"time_seconds": 1}})
    assert [b["step"] for b in
            history(open_channel(), "loss", {"every": {"step": 1}})] == [0]
