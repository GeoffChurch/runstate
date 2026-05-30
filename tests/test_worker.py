"""The reference worker loop (docs/design-v0.2.md §6).

The worker drains ``control.*``, registers/cancels subscriptions, and on each
``tick(step)`` services due ones by emitting ``value`` envelopes carrying its
current values. Tests run against both backends (the ``open_channel`` factory
shares one run across handles).
"""

from runstate.worker import Worker


def test_subscribe_then_tick_emits_current_value(open_channel):
    # an orchestrator subscribes to "loss", fire once now ({} schedule)
    orch = open_channel()
    orch.send({}, topic="control.subscribe", name="loss", request_id="r1")

    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 0.5)
    w.tick(step=10)

    vals = open_channel().read(topics=["value"])
    assert [(v.name, v.request_id, v.body) for v in vals] == [
        ("loss", "r1", {"value": 0.5, "step": 10})
    ]


def test_recurring_subscription_fires_each_due_tick(open_channel):
    orch = open_channel()
    orch.send({"every": {"step": 10}}, topic="control.subscribe", name="loss", request_id="r1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    for s in (0, 5, 10):
        w.tick(step=s)
    steps = [v.body["step"] for v in open_channel().read(topics=["value"])]
    assert steps == [0, 10]  # fires at registration and at +10, not at 5


def test_unsubscribe_stops_emissions(open_channel):
    orch = open_channel()
    orch.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=0)
    orch.send({}, topic="control.unsubscribe", request_id="r1")
    w.tick(step=1)
    w.tick(step=2)
    steps = [v.body["step"] for v in open_channel().read(topics=["value"])]
    assert steps == [0]  # only the fire before the unsubscribe landed


def test_control_stop_now(open_channel):
    orch = open_channel()
    orch.send({}, topic="control.stop", request_id="s1")
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.tick(step=5) == "commanded"


def test_control_stop_at_step(open_channel):
    orch = open_channel()
    orch.send({"from": {"step": 100}}, topic="control.stop", request_id="s1")
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.tick(step=50) is None
    assert w.tick(step=100) == "commanded"


def test_stopped_emits_dying_breath(open_channel):
    w = Worker(open_channel(), now=lambda: 0.0)
    w.stopped(reason="completed", final_step=500)
    e = open_channel().latest("lifecycle.stopped")
    assert e.body == {"reason": "completed", "final_step": 500}
    assert e.request_id is None  # broadcast — every observer sees it


def test_stopped_with_error(open_channel):
    w = Worker(open_channel(), now=lambda: 0.0)
    w.stopped(reason="errored", error="boom")
    assert open_channel().latest("lifecycle.stopped").body == {
        "reason": "errored",
        "error": "boom",
    }


def test_tick_emits_heartbeat_with_step_and_consumed_seq(open_channel):
    orch = open_channel()
    sub_seq = orch.send({}, topic="control.subscribe", name="loss", request_id="r1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=7)
    hb = open_channel().latest("lifecycle.heartbeat")
    # consumed_seq is the worker's read position in the inbound control order:
    # after draining, it has processed the subscribe at sub_seq.
    assert hb.body == {"step": 7, "consumed_seq": sub_seq}
    assert hb.request_id is None


def test_consumed_seq_advances_only_after_draining(open_channel):
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=0)  # nothing to drain yet
    assert open_channel().latest("lifecycle.heartbeat").body["consumed_seq"] == 0


def test_nak_when_until_already_satisfied(open_channel):
    orch = open_channel()
    orch.send({"until": {"step": 50}}, topic="control.subscribe", name="loss", request_id="r1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=100)  # already past `until` step 50 -> window closed, zero fires
    nak = open_channel().latest("lifecycle.nak")
    assert nak.request_id == "r1"
    assert nak.body["status"] == "unsatisfiable"
    assert open_channel().read(topics=["value"]) == []  # no value emitted


def test_nak_step_condition_on_stepless_worker(open_channel):
    orch = open_channel()
    orch.send({"from": {"step": 100}}, topic="control.subscribe", name="loss", request_id="r1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=None)  # stepless: a step threshold can never be satisfied
    assert open_channel().latest("lifecycle.nak").request_id == "r1"


def test_no_nak_for_a_future_step_on_a_stepped_worker(open_channel):
    orch = open_channel()
    orch.send({"from": {"step": 100}}, topic="control.subscribe", name="loss", request_id="r1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=50)  # not there yet — must NOT nak; it just waits
    assert open_channel().latest("lifecycle.nak") is None


def test_already_past_step_fires_at_current_step(open_channel):
    # the agreed clean >= semantics: subscribing past the threshold fires now,
    # at the current step (not a nak).
    orch = open_channel()
    orch.send({"from": {"step": 100}}, topic="control.subscribe", name="loss", request_id="r1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 0.5)
    w.tick(step=150)
    assert open_channel().latest("value", "loss").body == {"value": 0.5, "step": 150}
    assert open_channel().latest("lifecycle.nak") is None


def test_constructing_a_worker_emits_started_with_a_handle(open_channel):
    w = Worker(open_channel(), now=lambda: 0.0)
    e = open_channel().latest("lifecycle.started")
    assert e is not None
    assert e.body["handle"].startswith("local://")  # self-reported liveness handle
    assert e.body["attached_at"] == 0.0
    assert e.request_id is None
