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
    ch.send({"handle": local_handle(), "hostname": None, "attached_at": 0.0},
            topic="lifecycle.started")
    h = relaunch_if_needed(launcher, "r", lambda channel, **_: None, kwargs={})
    assert h is None
    assert launcher.open_channel("r").read(topics=["launcher.launched"]) == []  # no spawn


def _cell(channel, *, run_id, up_to, ckpt_dir):
    """A resumable worker: reads its run_id-keyed checkpoint, continues the
    run-absolute step, checkpoints the new frontier. Subscription-gated.

    Emits ``preempted`` (not ``completed``) so that ensure can re-drive it
    with a higher target — this worker is resumable-by-design and must not
    self-declare convergence with ``completed``."""
    ckpt = Path(ckpt_dir) / f"{run_id}.json"
    start = json.loads(ckpt.read_text())["next"] if ckpt.exists() else 0
    with runstate.Worker(channel, now=lambda: 0.0) as w:
        for step in w.steps(start=start, total=up_to):
            w.set("loss", float(step))
        w.stopped(reason="preempted")   # resumable: a higher up_to may be requested
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
    with pytest.raises(RuntimeError, match="failed"):
        ensure(producer, "loss", until={"step": 5})


def test_ensure_raises_when_run_makes_no_progress(tmp_path):
    launcher = runstate.ThreadLauncher()

    def stuck(channel, *, up_to):          # ignores up_to; always 2 steps, no ckpt
        with runstate.Worker(channel, now=lambda: 0.0) as w:
            for step in w.steps(total=2):
                w.set("loss", float(step))
            # Emits ``preempted`` (not ``completed``) so ensure re-drives it.
            # A ``completed`` stop would cause ensure to return early (new semantics);
            # ``preempted`` keeps ensure looping so the no-progress guard fires.
            w.stopped(reason="preempted")

    variant = runstate.Variant("exp", stuck, {"kwargs": {}})
    launcher.open_channel("exp").send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="obs"
    )
    producer = launch_producer(launcher, variant)
    with pytest.raises(RuntimeError, match="progress"):
        ensure(producer, "loss", until={"step": 5})


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
            # Emit ``preempted`` when stopping short (more steps may be requested),
            # let the Worker default ``completed`` only when reaching the full target.
            if stop < up_to:
                w.stopped(reason="preempted")
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
    with pytest.raises(RuntimeError, match="failed"):
        ensure(producer, "loss", until={"step": 5})


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

    # deterministically end the foreign episode BELOW target on the first sleep.
    # Use ``preempted`` (not ``completed``) so ensure re-drives it -- a
    # ``completed`` stop would cause ensure to return early under the new semantics.
    ended = {"done": False}

    def driver_sleep(_):
        if not ended["done"]:
            ended["done"] = True
            launcher.open_channel(rid).send(
                {"reason": "preempted", "error": None, "final_step": 1},
                topic="lifecycle.stopped",
            )

    series = ensure(producer, "loss", until={"step": 4}, sleep=driver_sleep)
    assert [b["step"] for b in series] == [0, 1, 2, 3]   # foreign 0,1 + re-driven 2,3 = one series


def test_public_exports_present():
    assert {"history", "ensure", "launch_producer", "relaunch_if_needed"} <= set(runstate.__all__)
    assert all(hasattr(runstate, n)
               for n in ("history", "ensure", "launch_producer", "relaunch_if_needed"))


# ---------------------------------------------------------------------------
# Synthetic-channel tests for the completed-short-of-up_to behaviour
# ---------------------------------------------------------------------------

class _FakeProducer:
    """A minimal duck-typed producer backed by a pre-seeded MemoryChannel.

    `extend` is a no-op (returns None, simulating a foreign episode) and
    counts how many times it was called.  The channel is shared so the caller
    can inspect / mutate it after construction.
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
        return None   # no handle: simulates a foreign/no-op extend


def _seed_episode(ch, *, heartbeat_step, stopped_reason, value_steps=None):
    """Write a completed single-episode lifecycle into *ch* (no live episode after)."""
    from runstate.vocabulary.handle import local_handle
    ch.send(
        {"handle": local_handle(), "hostname": None, "attached_at": 0.0},
        topic="lifecycle.started",
    )
    ch.send({"step": heartbeat_step, "consumed_seq": 0}, topic="lifecycle.heartbeat")
    if value_steps is not None:
        for s in value_steps:
            ch.send({"value": float(s), "step": s, "t": 0.0},
                    topic="value", name="loss")
    ch.send(
        {"reason": stopped_reason, "error": None, "final_step": heartbeat_step},
        topic="lifecycle.stopped",
    )


def test_ensure_completed_short_of_up_to_returns_without_redriving():
    """Test 1: a channel with completed short of up_to -> ensure returns; extend NOT called."""
    from runstate.channel.memory import MemoryChannel

    ch = MemoryChannel()
    K = 3
    _seed_episode(ch, heartbeat_step=K, stopped_reason="completed",
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
    _seed_episode(ch, heartbeat_step=K, stopped_reason="preempted",
                  value_steps=list(range(K + 1)))

    def _extend_side_effect(channel, target):
        """On the producer's first extend call, append a second episode that completes."""
        from runstate.vocabulary.handle import local_handle
        channel.send(
            {"handle": local_handle(), "hostname": None, "attached_at": 1.0},
            topic="lifecycle.started",
        )
        channel.send({"step": M, "consumed_seq": 0}, topic="lifecycle.heartbeat")
        for s in range(K + 1, M + 1):
            channel.send({"value": float(s), "step": s, "t": 1.0},
                         topic="value", name="loss")
        channel.send(
            {"reason": "completed", "error": None, "final_step": M},
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
        self._c.send({"reason": "preempted", "error": None, "final_step": 0},
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
    ch.send({"handle": local_handle(), "hostname": None, "attached_at": 0.0},
            topic="lifecycle.started")            # epoch 0.0
    ch.send({"step": 0, "consumed_seq": 0}, topic="lifecycle.heartbeat")  # 0 steps
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
    ch.send({"handle": local_handle(), "hostname": None, "attached_at": 0.0},
            topic="lifecycle.started")
    ch.send({"value": 0.0, "step": 0, "t": 0.0}, topic="value", name="loss")  # value.t frozen at 0
    ch.send({"step": 0, "consumed_seq": 0}, topic="lifecycle.heartbeat")

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
