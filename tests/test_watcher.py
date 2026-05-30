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
    assert w.poll("r") is None
    ch.send({"reason": "completed", "final_step": 5}, topic="lifecycle.stopped")
    r = w.poll("r")
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
    assert w.poll("r") is None  # fresh beacon
    clock[0] = 1020
    assert w.poll("r") is None  # 20s < 30s, still alive
    clock[0] = 1041
    r = w.poll("r")  # 41s since last beacon -> stale
    assert r.outcome == "presumed_dead"
    assert r.reason == "heartbeat_stale"


def test_staleness_tier_off_by_default():
    clock = [1000.0]
    ch = open_channel("r2", root=None, backend="memory")
    w = Watcher(now=lambda: clock[0])  # no heartbeat_timeout
    w.observe("r2", ch)
    clock[0] = 1_000_000
    assert w.poll("r2") is None  # never presumed dead on staleness alone


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
