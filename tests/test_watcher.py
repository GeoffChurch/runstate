"""Watcher: the stateful failure detector (docs/design-v0.2.md §8-9).

peek_terminal gives the record-based verdict (a terminal envelope exists). The
Watcher adds the inference-based tiers — probe the handle, and heartbeat
staleness — to produce "presumed_dead" for a worker that crashed or hung without
leaving a clean stop. poll() is the single non-blocking verdict (all tiers);
wait() loops poll() until terminal.
"""

from dataclasses import dataclass

import pytest

from runstate.channel import create_channel
from runstate.launcher import ThreadLauncher
from runstate.observables import MalformedRecordError
from runstate.watcher import Watcher, await_consumed
from runstate.worker import Worker


@dataclass
class FakeHandle:
    """A LaunchHandle test double with a fixed liveness answer."""

    run_id: str
    channel: object
    alive: bool
    handle: str = "local://fake/0"

    def is_alive(self) -> bool:
        return self.alive

    def wait(self, timeout=None):
        return None

    def terminate(self) -> None:
        pass


# ----- tiers 1-2: terminal record (delegated to peek_terminal), run_id stamped -----


def test_poll_none_while_running_then_terminal(tmp_path):
    ch = create_channel("r", root=tmp_path, backend="sqlite")
    w = Watcher()
    w.observe("r", ch)
    ch.send({"step": 0, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
    s = w.poll("r")
    assert s.done is False  # the Running arm of RunStatus
    assert s.step == 0  # carries the live snapshot from the heartbeat fold
    ch.send(
        {"completed": True, "error": None, "final_step": 5, "t": 0.0},
        topic="lifecycle.stopped",
    )
    r = w.poll("r")
    assert r.done is True
    assert r.outcome == "completed"
    assert r.run_id == "r"  # the Watcher stamps the run it knows
    assert r.final_step == 5


# ----- tier 3: probe the handle -----


def test_presumed_dead_via_probe(tmp_path):
    # the handle resolves dead and there's no terminal record on the log
    ch = create_channel("r", root=tmp_path, backend="sqlite")
    ch.send({"step": 3, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
    w = Watcher()
    w.add(FakeHandle(run_id="r", channel=ch, alive=False))
    r = w.poll("r")
    assert r.outcome == "presumed_dead"
    assert r.run_id == "r"


def test_clean_stop_beats_probe(tmp_path):
    # even if the handle says dead, a terminal record wins (it just exited)
    ch = create_channel("r", root=tmp_path, backend="sqlite")
    ch.send(
        {"completed": True, "error": None, "final_step": None, "t": 0.0},
        topic="lifecycle.stopped",
    )
    w = Watcher()
    w.add(FakeHandle(run_id="r", channel=ch, alive=False))
    assert w.poll("r").outcome == "completed"


# ----- tier 4: heartbeat staleness (injected clock) -----


def test_presumed_dead_via_heartbeat_staleness():
    clock = [1000.0]
    ch = create_channel("r", root=None, backend="memory")
    w = Watcher(now=lambda: clock[0], heartbeat_timeout=30)
    w.observe("r", ch)  # last_heartbeat_at initialized to 1000
    ch.send({"step": 0, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
    assert w.poll("r").done is False  # fresh beacon
    clock[0] = 1020
    assert w.poll("r").done is False  # 20s < 30s, still alive
    clock[0] = 1041
    r = w.poll("r")  # 41s since last beacon -> stale
    assert r.done is True
    assert r.outcome == "presumed_dead"
    assert r.reason == "heartbeat_stale"


def test_staleness_tier_off_by_default():
    clock = [1000.0]
    ch = create_channel("r2", root=None, backend="memory")
    w = Watcher(now=lambda: clock[0])  # no heartbeat_timeout
    w.observe("r2", ch)
    clock[0] = 1_000_000
    assert w.poll("r2").done is False  # never presumed dead on staleness alone


def test_cold_attach_reads_true_age_from_the_beacon_t():
    # The victim-1 regression (observer-clock §5): an observer attaches for the FIRST
    # time to a run that beaconed long ago and went quiet. The staleness clock seeds
    # from the beacon's OWN t (already on the log), so the run reads its true age on the
    # FIRST poll. Before the fix it seeded at registration (now()) -- and, crucially,
    # left last_hb_seq=0, so poll()'s _note_heartbeat mistook the old beacon for a fresh
    # arrival and clobbered the seed to now(): a 21-day-dead run read `Running`.
    ch = create_channel("cold", root=None, backend="memory")
    ch.send({"handle": "local://h/1", "t": 100.0}, topic="lifecycle.started")
    ch.send({"step": 5, "consumed_seq": 0, "t": 100.0}, topic="lifecycle.heartbeat")
    w = Watcher(now=lambda: 1000.0, heartbeat_timeout=30)  #  900s after the last beacon
    w.observe("cold", ch)
    r = w.poll("cold")  #                                     stale on the FIRST poll
    assert r.done is True
    assert r.outcome == "presumed_dead" and r.reason == "heartbeat_stale"


def test_cold_attach_then_a_fresh_beacon_upgrades_to_witnessed():
    # Seeded from an old beacon (stale), but a genuinely NEWER beacon arriving during
    # watching re-times skew-immunely off arrival (now()), never the beacon's own t --
    # the run is alive again, and its staleness clock now runs from when WE saw it.
    ch = create_channel("warm", root=None, backend="memory")
    ch.send({"handle": "local://h/1", "t": 100.0}, topic="lifecycle.started")
    ch.send({"step": 5, "consumed_seq": 0, "t": 100.0}, topic="lifecycle.heartbeat")
    clock = [1000.0]
    w = Watcher(now=lambda: clock[0], heartbeat_timeout=30)
    w.observe("warm", ch)
    assert w.poll("warm").done is True  #                    seeded old -> stale
    ch.send(
        {
            "step": 6,
            "consumed_seq": 0,
            "t": 200.0,
        },  #   a NEW beacon (t=200, far in the past)
        topic="lifecycle.heartbeat",
    )
    assert (
        w.poll("warm").done is False
    )  #                   witnessed at now()=1000 -> fresh, not t=200
    clock[0] = 1040
    assert (
        w.poll("warm").done is True
    )  #                    40s since that witnessed arrival -> stale


# ----- wait(): loop poll() until terminal -----


def test_wait_blocks_until_terminal(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)

    def _train(channel):
        with Worker(channel) as w:
            for _ in w.steps(total=3):
                pass
            w.stopped(completed=True)

    w = Watcher(poll_interval=0.005)
    h = launcher.launch("run", _train)
    w.add(h)
    r = w.wait("run")
    assert r.outcome == "completed"
    assert r.run_id == "run"


def test_wait_timeout_raises():
    clock = [0.0]
    ch = create_channel("slow", root=None, backend="memory")
    w = Watcher(
        now=lambda: clock[0],
        sleep=lambda s: clock.__setitem__(0, clock[0] + s),
        poll_interval=1.0,
    )
    w.observe("slow", ch)  # never reaches a terminal record
    with pytest.raises(TimeoutError):
        w.wait("slow", timeout=5.0)


# ----- iter_events(): stream new envelopes across tracked runs -----


def test_iter_events_streams_then_continues_from_cursor():
    ch = create_channel("r", root=None, backend="memory")
    w = Watcher()
    w.observe("r", ch)
    ch.send({"a": 1}, topic="value", name="x")
    ch.send(
        {"completed": True, "error": None, "final_step": None, "t": 0.0},
        topic="lifecycle.stopped",
    )
    first = list(w.iter_events(timeout=0))
    assert [(rid, e.topic) for rid, e in first] == [
        ("r", "value"),
        ("r", "lifecycle.stopped"),
    ]
    # a second drain starts where the first left off (per-run cursor)
    ch.send({"b": 2}, topic="value", name="y")
    second = list(w.iter_events(timeout=0))
    assert [(rid, e.topic) for rid, e in second] == [("r", "value")]


def test_iter_events_spans_multiple_runs():
    a = create_channel("a", root=None, backend="memory")
    b = create_channel("b", root=None, backend="memory")
    w = Watcher()
    w.observe("a", a)
    w.observe("b", b)
    a.send({}, topic="value", name="x")
    b.send({}, topic="value", name="y")
    run_ids = {rid for rid, _ in w.iter_events(timeout=0)}
    assert run_ids == {"a", "b"}


def test_wait_streams_events_via_on_event(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)

    def _train(channel):
        with Worker(channel) as worker:
            for _ in worker.steps(total=2):
                pass
            worker.stopped(completed=True)

    w = Watcher(poll_interval=0.005)
    w.add(launcher.launch("run", _train))
    seen = []
    r = w.wait("run", on_event=lambda rid, e: seen.append((rid, e.topic)))
    assert r.outcome == "completed"
    topics = [t for _, t in seen]
    assert "lifecycle.started" in topics
    assert "lifecycle.stopped" in topics


# ----- wait_all(): total dict of RunStatus across runs -----


def test_wait_all_returns_all_terminal(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)

    def _ok(channel):
        with Worker(channel) as worker:
            for _ in worker.steps(total=1):
                pass
            worker.stopped(completed=True)

    w = Watcher(poll_interval=0.005)
    w.add(launcher.launch("a", _ok))
    w.add(launcher.launch("b", _ok))
    res = w.wait_all()
    assert set(res) == {"a", "b"}
    assert all(s.done and s.outcome == "completed" for s in res.values())


def test_wait_all_capped_reports_pending_as_running():
    clock = [0.0]
    a = create_channel("a", root=None, backend="memory")
    w = Watcher(
        now=lambda: clock[0],
        sleep=lambda s: clock.__setitem__(0, clock[0] + s),
        poll_interval=1.0,
    )
    w.observe("a", a)
    a.send(
        {"step": 7, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat"
    )  # alive, never terminal
    res = w.wait_all(timeout=5.0)
    assert set(res) == {"a"}  # total over tracked runs
    s = res["a"]
    assert s.done is False  # pending == the Running arm, not absence/None
    assert s.step == 7  # tells you where the slow run is
    assert s.beacon_age == 5.0  # ...and how stale (now 5.0 - last beacon at 0.0)


# ----- broadcast(): fan one subscription across runs with a shared request_id -----


def test_broadcast_fans_subscription_with_shared_request_id():
    a = create_channel("a", root=None, backend="memory")
    b = create_channel("b", root=None, backend="memory")
    w = Watcher()
    w.observe("a", a)
    w.observe("b", b)
    rid = w.broadcast("loss", {"from": {"step": 100}})
    for ch in (a, b):
        sub = ch.latest("control.subscribe")
        assert sub.name == "loss"
        assert sub.request_id == rid  # the shared correlation id
        assert sub.body == {"from": {"step": 100}}


# ----- round-2 review fixes -----


def test_cold_attach_without_timeout_reports_the_seeded_step():
    # no timeout: still Running, but the snapshot reflects the seeded beacon --
    # the cold observer sees where the run got to. (The seeded age itself is
    # pinned by test_cold_attach_reads_true_age_from_the_beacon_t; this pins the
    # STEP half: dropping the last_step seed regresses Running.step to None,
    # observer-clock §5.)
    ch = create_channel("cold2", root=None, backend="memory")
    ch.send({"handle": "local://h/1", "t": 100.0}, topic="lifecycle.started")
    ch.send({"step": 41, "consumed_seq": 0, "t": 100.0}, topic="lifecycle.heartbeat")
    w = Watcher(now=lambda: 1000.0)  #  no heartbeat_timeout
    w.observe("cold2", ch)
    s = w.poll("cold2")
    assert s.done is False
    assert s.step == 41  #  seeded from the prefix beacon


def test_future_dated_seeded_beacon_reads_conservative_live():
    # a future-dated beacon (worker clock ahead of ours) yields a NEGATIVE age --
    # the one unambiguous "my cross-clock estimate is broken" signal. It lands
    # conservative-LIVE via the existing path, the safe direction (observer-clock
    # §5): no special handling -- an abs(beacon_age) "fix" here would turn the
    # broken-estimate signal into a spurious presumed_dead.
    ch = create_channel("future", root=None, backend="memory")
    ch.send({"handle": "local://h/1", "t": 5000.0}, topic="lifecycle.started")
    ch.send({"step": 5, "consumed_seq": 0, "t": 5000.0}, topic="lifecycle.heartbeat")
    w = Watcher(now=lambda: 1000.0, heartbeat_timeout=30)
    w.observe("future", ch)  #  beacon t=5000 is ahead of now=1000
    s = w.poll("future")
    assert s.done is False  #  negative age -> not stale (conservative)
    assert s.beacon_age == -4000.0


def test_cold_attach_junk_t_beacon_falls_back_to_now_seed():
    # a junk/unmigrated t on the newest beacon earns no seed: fall back to now()
    # (measurement-plane tolerance), exactly the pre-clock behavior -- so a cold
    # observer of a junk beacon reads Running until the timeout elapses from NOW.
    clock = [1000.0]
    ch = create_channel("junkseed", root=None, backend="memory")
    ch.send(
        {"step": 5, "consumed_seq": 0}, topic="lifecycle.heartbeat"
    )  #  no t (unmigrated)
    w = Watcher(now=lambda: clock[0], heartbeat_timeout=30)
    w.observe("junkseed", ch)  #  seed falls back to now()=1000
    assert w.poll("junkseed").done is False  #  not stale: seeded at now(), not an old t
    clock[0] = 1031
    assert w.poll("junkseed").outcome == "presumed_dead"  #  31s from the now()-seed


def test_staleness_clock_resets_on_each_new_beacon():
    # the central property: a worker that keeps beaconing is NOT declared dead,
    # however long since registration -- each new beacon restarts the clock.
    clock = [1000.0]
    ch = create_channel("alive", root=None, backend="memory")
    w = Watcher(now=lambda: clock[0], heartbeat_timeout=30)
    w.observe("alive", ch)
    ch.send({"step": 0, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
    clock[0] = 1025
    assert w.poll("alive").done is False  # notes beacon 1
    ch.send({"step": 1, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
    clock[0] = 1050  # 50s since registration, but the clock reset on beacon 2
    assert w.poll("alive").done is False  # would be presumed_dead if reset were dropped
    clock[0] = 1081  # 31s since the last beacon, none newer
    assert w.poll("alive").outcome == "presumed_dead"


def test_staleness_boundary_is_strict():
    # beacon_age == timeout is alive; just over is dead (the `>` not `>=`).
    clock = [1000.0]
    ch = create_channel("edge", root=None, backend="memory")
    w = Watcher(now=lambda: clock[0], heartbeat_timeout=30)
    w.observe("edge", ch)
    ch.send({"step": 0, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
    w.poll("edge")  # note the beacon at t=1000
    clock[0] = 1030  # exactly the timeout
    assert w.poll("edge").done is False
    clock[0] = 1031
    assert w.poll("edge").outcome == "presumed_dead"


def test_staleness_counts_from_when_watching_began():
    # the floor detector catches a worker that never beacons (crashed/hung during
    # startup), not just a mid-run hang -- the clock runs from when we began
    # watching. Legit-slow startup is the caller's tuning concern (raise the
    # timeout, or start watching after the first beacon).
    clock = [1000.0]
    ch = create_channel("boot", root=None, backend="memory")
    w = Watcher(now=lambda: clock[0], heartbeat_timeout=30)
    w.observe("boot", ch)  # never beacons
    clock[0] = 1010
    assert w.poll("boot").done is False  # within the timeout
    clock[0] = 1031
    assert w.poll("boot").outcome == "presumed_dead"  # startup death is caught


def test_watcher_reaps_dead_handle_for_a_precise_verdict(tmp_path):
    # a crashed subprocess (no clean stop): the Watcher should reap it so the
    # manner of death lands on the log and the verdict is precise (errored),
    # not a bare presumed_dead that discards the exit code.
    import sys

    from runstate.launcher import LocalLauncher

    launcher = LocalLauncher(root=tmp_path)
    h = launcher.launch("crash", [sys.executable, "-c", "import os; os._exit(42)"])
    w = Watcher(poll_interval=0.005)
    w.add(h)
    r = w.wait("crash")
    assert r.outcome == "errored"
    assert h.channel.latest("launcher.terminated").body["exit_code"] == 42


def test_wait_does_a_final_drain_after_terminal():
    # an envelope arriving right as the terminal verdict is reached must still
    # reach on_event (a final drain after done), not be cut off.
    ch = create_channel("r", root=None, backend="memory")
    ch.send(
        {"completed": True, "error": None, "final_step": None, "t": 0.0},
        topic="lifecycle.stopped",
    )
    w = Watcher()
    w.observe("r", ch)
    seen = []

    def on_event(rid, e):
        seen.append(e.topic)
        if e.topic == "lifecycle.stopped" and len(seen) == 1:
            # trailing envelope appears after the drain that delivered the stop
            ch.send(
                {"reason": "exited", "exit_code": 0, "signal": None, "t": 0.0},
                topic="launcher.terminated",
            )

    w.wait("r", on_event=on_event)
    assert "launcher.terminated" in seen


# ----- await_consumed(): control-ack helper -----


def test_await_consumed_returns_none_when_accepted(open_run):
    ch = open_run()
    s = ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r"
    )
    ch.send({"step": 0, "consumed_seq": s, "t": 0.0}, topic="lifecycle.heartbeat")
    assert await_consumed(open_run(), s, request_id="r") is None


def test_await_consumed_returns_the_nak_when_refused(open_run):
    ch = open_run()
    s = ch.send(
        {"until": {"step": 0}}, topic="control.subscribe", name="loss", request_id="r"
    )
    ch.send(
        {"reason": "unsatisfiable", "message": "no fires"},
        topic="lifecycle.nak",
        request_id="r",
    )
    ch.send({"step": 0, "consumed_seq": s, "t": 0.0}, topic="lifecycle.heartbeat")
    nak = await_consumed(open_run(), s, request_id="r")
    assert nak is not None and nak.reason == "unsatisfiable"


def test_await_consumed_times_out_if_not_consumed(open_run):
    import pytest

    ch = open_run()
    s = ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r"
    )
    with pytest.raises(TimeoutError):
        await_consumed(open_run(), s, request_id="r", timeout=0.0, now=lambda: 0.0)


def test_await_consumed_blocks_below_watermark_then_returns_when_it_advances(
    open_run,
):
    # a heartbeat exists but BELOW the watermark -> must keep waiting, not return early
    ch = open_run()
    s = ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r"
    )
    ch.send({"step": 0, "consumed_seq": s - 1, "t": 0.0}, topic="lifecycle.heartbeat")
    advanced = {"done": False}

    def driver_sleep(_):  # on the first poll, advance consumed_seq to s
        if not advanced["done"]:
            advanced["done"] = True
            ch.send(
                {"step": 1, "consumed_seq": s, "t": 0.0}, topic="lifecycle.heartbeat"
            )

    assert await_consumed(open_run(), s, request_id="r", sleep=driver_sleep) is None
    assert advanced[
        "done"
    ]  # it actually blocked until the watermark advanced (not a premature return)


def test_await_consumed_resolves_a_nak_before_the_watermark(open_run):
    # answer-first (specs/service-worker.md): the retire-win path -- the nak
    # lands, the worker dies, no heartbeat ever carries the watermark; the
    # waiter must not deadlock on a question the log already answers.
    ch = open_run()
    s = ch.send(
        {"until": {"step": 0}}, topic="control.subscribe", name="loss", request_id="r"
    )
    ch.send(
        {"reason": "unsatisfiable", "message": "no fires"},
        topic="lifecycle.nak",
        request_id="r",
    )
    nak = await_consumed(open_run(), s, request_id="r", timeout=1.0)
    assert nak is not None and nak.reason == "unsatisfiable"


def test_await_consumed_ignores_an_earlier_nak_for_the_same_id(open_run):
    # positional: a nak that PRECEDES the request answers nothing.
    ch = open_run()
    ch.send(
        {"reason": "malformed", "message": "old"}, topic="lifecycle.nak", request_id="r"
    )
    s = ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r"
    )
    ch.send({"step": 0, "consumed_seq": s, "t": 0.0}, topic="lifecycle.heartbeat")
    assert await_consumed(open_run(), s, request_id="r") is None


def test_await_consumed_resolves_refused_by_death(open_run):
    # a terminal stopped FOLLOWS the request with no later episode: no worker
    # will ever drain it -- return the terminal RunResult instead of blocking.
    from runstate import RunResult

    ch = open_run()
    s = ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r"
    )
    ch.send(
        {"completed": False, "error": None, "final_step": 3, "t": 0.0},
        topic="lifecycle.stopped",
    )
    r = await_consumed(open_run(), s, request_id="r", timeout=1.0)
    assert isinstance(r, RunResult) and r.outcome == "preempted"


def test_await_consumed_typed_error_on_malformed_nak(open_run):
    # the nak parse is on the verdict plane: an uninterpretable answer raises
    # the typed MalformedRecordError, not a bare TypeError from Nak(**body).
    ch = open_run()
    s = ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r"
    )
    nak_seq = ch.send(
        {"reason": "malformed"}, topic="lifecycle.nak", request_id="r"
    )  # no message
    with pytest.raises(MalformedRecordError) as ei:
        await_consumed(open_run(), s, request_id="r", timeout=1.0)
    assert ei.value.seq == nak_seq
    assert ei.value.topic == "lifecycle.nak"


def test_await_consumed_keeps_waiting_when_death_precedes_the_request(open_run):
    # the request landed AFTER the death: it correctly awaits the next episode
    # (lazy-launch's case) -- a timeout, never refused-by-death.
    import pytest

    ch = open_run()
    ch.send(
        {"completed": False, "error": None, "final_step": 3, "t": 0.0},
        topic="lifecycle.stopped",
    )
    s = ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r"
    )
    with pytest.raises(TimeoutError):
        await_consumed(open_run(), s, request_id="r", timeout=0.0, now=lambda: 0.0)


def test_poll_skips_junk_heartbeat_body(tmp_path):
    # heartbeats are measurement-plane: a junk body earns no liveness credit
    # and never crashes poll; the next conforming beacon lands normally.
    ch = create_channel("r", root=tmp_path, backend="sqlite")
    w = Watcher()
    w.observe("r", ch)
    ch.send({"beat": "junk"}, topic="lifecycle.heartbeat")
    s = w.poll("r")
    assert s.done is False
    assert s.step is None  # the junk record contributed nothing
    ch.send({"step": 7, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
    assert w.poll("r").step == 7


def test_await_consumed_ignores_junk_heartbeat_watermark(open_run):
    # a junk beacon is no watermark evidence: keep waiting (timeout), never a
    # bare TypeError from Heartbeat(**body).
    import pytest

    ch = open_run()
    s = ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r"
    )
    ch.send({"beat": "junk"}, topic="lifecycle.heartbeat")
    with pytest.raises(TimeoutError):
        await_consumed(open_run(), s, request_id="r", timeout=0.0, now=lambda: 0.0)


def test_poll_skips_wrong_typed_heartbeat_step(tmp_path):
    # type-junk is junk too: a non-int step earns no measurement credit and
    # never leaks a str into Running.step.
    ch = create_channel("r", root=tmp_path, backend="sqlite")
    w = Watcher()
    w.observe("r", ch)
    ch.send({"step": "abc", "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
    s = w.poll("r")
    assert s.done is False
    assert s.step is None
    ch.send({"step": 7, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
    assert w.poll("r").step == 7
