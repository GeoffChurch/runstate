"""Watcher: the stateful failure detector (docs/design-v0.2.md §8-9).

peek_terminal gives the record-based verdict (a terminal envelope exists). The
Watcher adds the inference-based tiers — probe the handle, and heartbeat
staleness — to produce "presumed_dead" for a worker that crashed or hung without
leaving a clean stop. poll() is the single non-blocking verdict (all tiers);
wait() loops poll() until terminal.
"""

from dataclasses import dataclass

import pytest

from runstate.channel import open_channel
from runstate.launcher import ThreadLauncher
from runstate.watcher import Watcher
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
    ch = open_channel("r", root=tmp_path, backend="sqlite")
    w = Watcher()
    w.observe("r", ch)
    ch.send({"step": 0, "consumed_seq": 0}, topic="lifecycle.heartbeat")
    s = w.poll("r")
    assert s.done is False  # the Running arm of RunStatus
    assert s.step == 0  # carries the live snapshot from the heartbeat fold
    ch.send({"reason": "completed", "final_step": 5}, topic="lifecycle.stopped")
    r = w.poll("r")
    assert r.done is True
    assert r.outcome == "completed"
    assert r.run_id == "r"  # the Watcher stamps the run it knows
    assert r.final_step == 5


# ----- tier 3: probe the handle -----


def test_presumed_dead_via_probe(tmp_path):
    # the handle resolves dead and there's no terminal record on the log
    ch = open_channel("r", root=tmp_path, backend="sqlite")
    ch.send({"step": 3, "consumed_seq": 0}, topic="lifecycle.heartbeat")
    w = Watcher()
    w.add(FakeHandle(run_id="r", channel=ch, alive=False))
    r = w.poll("r")
    assert r.outcome == "presumed_dead"
    assert r.run_id == "r"


def test_clean_stop_beats_probe(tmp_path):
    # even if the handle says dead, a terminal record wins (it just exited)
    ch = open_channel("r", root=tmp_path, backend="sqlite")
    ch.send({"reason": "completed"}, topic="lifecycle.stopped")
    w = Watcher()
    w.add(FakeHandle(run_id="r", channel=ch, alive=False))
    assert w.poll("r").outcome == "completed"


# ----- tier 4: heartbeat staleness (injected clock) -----


def test_presumed_dead_via_heartbeat_staleness():
    clock = [1000.0]
    ch = open_channel("r", root=None, backend="memory")
    w = Watcher(now=lambda: clock[0], heartbeat_timeout=30)
    w.observe("r", ch)  # last_heartbeat_at initialized to 1000
    ch.send({"step": 0}, topic="lifecycle.heartbeat")
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
    ch = open_channel("r2", root=None, backend="memory")
    w = Watcher(now=lambda: clock[0])  # no heartbeat_timeout
    w.observe("r2", ch)
    clock[0] = 1_000_000
    assert w.poll("r2").done is False  # never presumed dead on staleness alone


# ----- wait(): loop poll() until terminal -----


def test_wait_blocks_until_terminal(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)

    def _train(channel):
        with Worker(channel) as w:
            for _ in w.steps(total=3):
                pass

    w = Watcher(poll_interval=0.005)
    h = launcher.launch("run", _train)
    w.add(h)
    r = w.wait("run")
    assert r.outcome == "completed"
    assert r.run_id == "run"


def test_wait_timeout_raises():
    clock = [0.0]
    ch = open_channel("slow", root=None, backend="memory")
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
    ch = open_channel("r", root=None, backend="memory")
    w = Watcher()
    w.observe("r", ch)
    ch.send({"a": 1}, topic="value", name="x")
    ch.send({"reason": "completed"}, topic="lifecycle.stopped")
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
    a = open_channel("a", root=None, backend="memory")
    b = open_channel("b", root=None, backend="memory")
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

    w = Watcher(poll_interval=0.005)
    w.add(launcher.launch("a", _ok))
    w.add(launcher.launch("b", _ok))
    res = w.wait_all()
    assert set(res) == {"a", "b"}
    assert all(s.done and s.outcome == "completed" for s in res.values())


def test_wait_all_capped_reports_pending_as_running():
    clock = [0.0]
    a = open_channel("a", root=None, backend="memory")
    w = Watcher(
        now=lambda: clock[0],
        sleep=lambda s: clock.__setitem__(0, clock[0] + s),
        poll_interval=1.0,
    )
    w.observe("a", a)
    a.send({"step": 7}, topic="lifecycle.heartbeat")  # alive, never terminal
    res = w.wait_all(timeout=5.0)
    assert set(res) == {"a"}  # total over tracked runs
    s = res["a"]
    assert s.done is False  # pending == the Running arm, not absence/None
    assert s.step == 7  # tells you where the slow run is
    assert s.beacon_age == 5.0  # ...and how stale (now 5.0 - last beacon at 0.0)


# ----- broadcast(): fan one subscription across runs with a shared request_id -----


def test_broadcast_fans_subscription_with_shared_request_id():
    a = open_channel("a", root=None, backend="memory")
    b = open_channel("b", root=None, backend="memory")
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


def test_staleness_clock_resets_on_each_new_beacon():
    # the central property: a worker that keeps beaconing is NOT declared dead,
    # however long since registration -- each new beacon restarts the clock.
    clock = [1000.0]
    ch = open_channel("alive", root=None, backend="memory")
    w = Watcher(now=lambda: clock[0], heartbeat_timeout=30)
    w.observe("alive", ch)
    ch.send({"step": 0}, topic="lifecycle.heartbeat")
    clock[0] = 1025
    assert w.poll("alive").done is False  # notes beacon 1
    ch.send({"step": 1}, topic="lifecycle.heartbeat")
    clock[0] = 1050  # 50s since registration, but the clock reset on beacon 2
    assert w.poll("alive").done is False  # would be presumed_dead if reset were dropped
    clock[0] = 1081  # 31s since the last beacon, none newer
    assert w.poll("alive").outcome == "presumed_dead"


def test_staleness_boundary_is_strict():
    # beacon_age == timeout is alive; just over is dead (the `>` not `>=`).
    clock = [1000.0]
    ch = open_channel("edge", root=None, backend="memory")
    w = Watcher(now=lambda: clock[0], heartbeat_timeout=30)
    w.observe("edge", ch)
    ch.send({"step": 0}, topic="lifecycle.heartbeat")
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
    ch = open_channel("boot", root=None, backend="memory")
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
    ch = open_channel("r", root=None, backend="memory")
    ch.send({"reason": "completed"}, topic="lifecycle.stopped")
    w = Watcher()
    w.observe("r", ch)
    seen = []

    def on_event(rid, e):
        seen.append(e.topic)
        if e.topic == "lifecycle.stopped" and len(seen) == 1:
            # trailing envelope appears after the drain that delivered the stop
            ch.send({"exit_code": 0, "reason": "exited"}, topic="launcher.terminated")

    w.wait("r", on_event=on_event)
    assert "launcher.terminated" in seen
