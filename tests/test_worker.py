"""The reference worker loop (docs/design-v0.2.md §6).

The worker drains ``control.*``, registers/cancels subscriptions, and on each
``tick(step)`` services due ones by emitting ``value`` envelopes carrying its
current values. Tests run against both backends (the ``open_channel`` factory
shares one run across handles).
"""

import pytest

from runstate.worker import Worker
from runstate.vocabulary.handle import local_handle


def test_subscribe_then_tick_emits_current_value(open_channel):
    # an orchestrator subscribes to "loss", fire once now ({} schedule)
    orch = open_channel()
    orch.send({}, topic="control.subscribe", name="loss", request_id="r1")

    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 0.5)
    w.tick(step=10)

    vals = open_channel().read(topics=["value"])
    assert [(v.name, v.request_id, v.body) for v in vals] == [
        ("loss", "r1", {"value": 0.5, "step": 10, "t": 0.0})
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
    assert e.body == {"reason": "completed", "error": None, "final_step": 500}
    assert e.request_id is None  # broadcast — every observer sees it


def test_stopped_with_error(open_channel):
    w = Worker(open_channel(), now=lambda: 0.0)
    w.stopped(reason="errored", error="boom")
    assert open_channel().latest("lifecycle.stopped").body == {
        "reason": "errored",
        "error": "boom",
        "final_step": None,
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
    assert nak.body["reason"] == "unsatisfiable"
    assert open_channel().read(topics=["value"]) == []  # no value emitted


def test_nak_malformed_schedule_does_not_kill_the_worker(open_channel):
    # a schema-invalid body (unknown condition) must be refused, not fatal
    orch = open_channel()
    orch.send({"from": {}}, topic="control.subscribe", name="loss", request_id="bad")
    orch.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="ok")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=0)  # must not raise
    naks = open_channel().read(topics=["lifecycle.nak"], request_ids=["bad"])
    assert naks[0].body["reason"] == "malformed"
    # the well-formed sibling still gets serviced -- one bad request isn't fatal
    assert open_channel().latest("value", "loss").request_id == "ok"


def test_nak_malformed_stop_from_does_not_crash(open_channel):
    # a malformed `from` on a stop must nak (like a malformed subscribe), not
    # crash the worker -- and must not poison self._stop and re-crash every tick.
    orch = open_channel()
    orch.send({"from": {"step": "oops"}}, topic="control.stop", request_id="s1")
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.tick(step=0) is None  # survives, does not stop
    assert w.tick(step=1) is None  # not poisoned
    nak = open_channel().latest("lifecycle.nak")
    assert nak.request_id == "s1"
    assert nak.body["reason"] == "malformed"


def test_nak_stop_unsatisfiable_on_stepless_worker(open_channel):
    # a step-from stop sent to a stepless worker can never fire -> nak (parity
    # with subscribe), rather than silently never auto-stopping.
    orch = open_channel()
    orch.send({"from": {"step": 100}}, topic="control.stop", request_id="s1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=None)  # stepless
    nak = open_channel().latest("lifecycle.nak")
    assert nak.request_id == "s1"
    assert nak.body["reason"] == "unsatisfiable"


def test_stepped_step_from_stop_still_fires(open_channel):
    # the parity nak must not catch a legitimate stepped stop (clean >= fires)
    orch = open_channel()
    orch.send({"from": {"step": 2}}, topic="control.stop", request_id="s1")
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.tick(step=0) is None
    assert w.tick(step=2) == "commanded"
    assert open_channel().latest("lifecycle.nak") is None


def test_nak_stop_with_every_or_until(open_channel):
    orch = open_channel()
    # a stop is one-shot; every/until are rejected, not silently honored
    orch.send({"until": {"step": 999}}, topic="control.stop", request_id="s1")
    w = Worker(open_channel(), now=lambda: 0.0)
    reason = w.tick(step=0)  # must NOT stop on a malformed stop
    assert reason is None
    nak = open_channel().latest("lifecycle.nak")
    assert nak.body["reason"] == "malformed"


def test_nak_unsupported_control_verb(open_channel):
    orch = open_channel()
    orch.send({}, topic="control.frobnicate", request_id="r1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=0)
    nak = open_channel().latest("lifecycle.nak")
    assert nak.request_id == "r1"
    assert nak.body["reason"] == "unsupported"


def test_nak_subscribe_without_request_id(open_channel):
    orch = open_channel()
    orch.send({"every": {"step": 1}}, topic="control.subscribe", name="loss")  # no request_id
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=0)
    nak = open_channel().latest("lifecycle.nak")
    assert nak.body["reason"] == "malformed"
    assert open_channel().read(topics=["value"]) == []  # nothing registered/emitted


def test_unserializable_value_fails_clearly_naming_the_metric(tmp_path):
    from runstate.channel import open_channel as oc

    ch = oc("r", root=tmp_path, backend="sqlite")  # default json_default=None
    ch.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="o")
    w = Worker(ch, now=lambda: 0.0)
    w.set("loss", {1, 2})  # a set isn't JSON-serializable
    with pytest.raises(TypeError, match="'loss'.*not.*JSON-serializable"):
        w.tick(step=0)


def test_json_default_hook_coerces_exotic_values(tmp_path):
    from runstate.channel import open_channel as oc

    # the sender-side hook coerces a set -> sorted list so it round-trips
    ch = oc("r", root=tmp_path, backend="sqlite", json_default=sorted)
    ch.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="o")
    w = Worker(ch, now=lambda: 0.0)
    w.set("loss", {3, 1, 2})
    w.tick(step=0)
    assert ch.latest("value", "loss").body["value"] == [1, 2, 3]


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
    assert open_channel().latest("value", "loss").body == {"value": 0.5, "step": 150, "t": 0.0}
    assert open_channel().latest("lifecycle.nak") is None


def test_constructing_a_worker_emits_started_with_a_handle(open_channel):
    w = Worker(open_channel(), now=lambda: 0.0)
    e = open_channel().latest("lifecycle.started")
    assert e is not None
    assert e.body["handle"].startswith("local://")  # self-reported liveness handle
    assert e.body["attached_at"] == 0.0
    assert e.request_id is None


def test_steps_drives_ticks_and_stops_completed(open_channel):
    orch = open_channel()
    orch.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r1")
    with Worker(open_channel(), now=lambda: 0.0) as w:
        for step in w.steps(3):
            w.set("loss", float(step))
    obs = open_channel()
    assert [v.body for v in obs.read(topics=["value"])] == [
        {"value": 0.0, "step": 0, "t": 0.0},
        {"value": 1.0, "step": 1, "t": 0.0},
        {"value": 2.0, "step": 2, "t": 0.0},
    ]
    assert obs.latest("lifecycle.stopped").body == {"reason": "completed", "error": None, "final_step": 2}


def test_steps_breaks_on_commanded_stop(open_channel):
    orch = open_channel()
    orch.send({"from": {"step": 2}}, topic="control.stop", request_id="s1")
    seen = []
    with Worker(open_channel(), now=lambda: 0.0) as w:
        for step in w.steps(10):
            seen.append(step)
    assert seen == [0, 1, 2]  # stops at the commanded step, never reaches 3..9
    assert open_channel().latest("lifecycle.stopped").body == {
        "reason": "commanded",
        "error": None,
        "final_step": 2,
    }


def test_context_manager_reports_errored_on_exception(open_channel):
    with pytest.raises(ValueError):
        with Worker(open_channel(), now=lambda: 0.0) as w:
            for step in w.steps(10):
                if step == 1:
                    raise ValueError("boom")
    e = open_channel().latest("lifecycle.stopped")
    assert e.body == {"reason": "errored", "error": "boom", "final_step": 1}


def test_stopped_is_idempotent(open_channel):
    w = Worker(open_channel(), now=lambda: 0.0)
    w.stopped(reason="completed")
    w.stopped(reason="errored")  # second call is a no-op
    stops = open_channel().read(topics=["lifecycle.stopped"])
    assert len(stops) == 1 and stops[0].body == {"reason": "completed", "error": None, "final_step": None}


def test_second_worker_loses_the_claim_and_does_no_work(open_channel):
    ch = open_channel()
    # a live episode already exists: a started by *our* pid (resolves alive), no stopped
    ch.send({"handle": local_handle(), "hostname": None, "attached_at": 0.0},
            topic="lifecycle.started")
    with Worker(open_channel(), now=lambda: 0.0) as w:
        assert w.claimed is False                            # lost: an episode is already live
        for step in w.steps(total=3):
            w.set("loss", float(step))                       # body must not run
    assert open_channel().read(topics=["value"]) == []       # the loser emitted no values
    assert len(open_channel().read(topics=["lifecycle.started"])) == 1  # no second started


def test_steps_resumes_at_start_with_run_absolute_step(open_channel):
    orch = open_channel()
    orch.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r")
    with Worker(open_channel(), now=lambda: 0.0) as w:
        for step in w.steps(start=5, total=8):
            w.set("loss", float(step))
    steps = [v.body["step"] for v in open_channel().read(topics=["value"])]
    assert steps == [5, 6, 7]                                   # run-absolute, not 0,1,2
    assert open_channel().latest("lifecycle.stopped").body["final_step"] == 7
