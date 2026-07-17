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
    orch.send(
        {"every": {"step": 10}}, topic="control.subscribe", name="loss", request_id="r1"
    )
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    for s in (0, 5, 10):
        w.tick(step=s)
    steps = [v.body["step"] for v in open_channel().read(topics=["value"])]
    assert steps == [0, 10]  # fires at registration and at +10, not at 5


def test_unsubscribe_stops_emissions(open_channel):
    orch = open_channel()
    orch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r1"
    )
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
    assert w.tick(step=5) is True


def test_control_stop_at_step(open_channel):
    orch = open_channel()
    orch.send({"from": {"step": 100}}, topic="control.stop", request_id="s1")
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.tick(step=50) is False
    assert w.tick(step=100) is True


def test_commanded_stop_latches_until_honored(open_channel):
    # The stop decision is a monotone level, not a consumed-once pulse
    # (specs/stop-discharge.md S1): a host loop that cannot act on one True --
    # a callback-guest whose loop ignores tick's return -- recovers it at the
    # next safe point instead of losing the stop forever.
    orch = open_channel()
    orch.send({"from": {"step": 5}}, topic="control.stop", request_id="s1")
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.tick(step=5) is True
    assert w.tick(step=6) is True  #  the missed True is still there
    assert w.tick(step=7) is True


def test_stop_pending_is_a_side_effect_free_poll(open_channel):
    # A callback-guest polls the same decision at its own safe point
    # (specs/stop-discharge.md): reading the level consumes nothing, and the
    # tick return is undented by any number of polls.
    orch = open_channel()
    orch.send({}, topic="control.stop", request_id="s1")
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.stop_pending is False  #  nothing drained yet -- no pending stop
    assert w.tick(step=0) is True
    assert w.stop_pending is True
    assert w.stop_pending is True  #   polling consumed nothing...
    assert w.tick(step=1) is True  #   ...and neither did it dent the decision


def test_stop_pending_tracks_the_last_safe_point(open_channel):
    # The property is the SAME predicate tick returns, evaluated at the
    # worker's last safe point -- a step-keyed stop reads False before its
    # step and True from it on, in agreement with the tick that just ran.
    orch = open_channel()
    orch.send({"from": {"step": 5}}, topic="control.stop", request_id="s1")
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.tick(step=3) is False
    assert w.stop_pending is False
    assert w.tick(step=5) is True
    assert w.stop_pending is True


def test_two_pending_stops_or_join(open_channel):
    # Pending stops are a set, not a slot (specs/stop-discharge.md S4): a
    # later stop must not clobber an earlier still-pending one (audit F3).
    # The combined decision is the condition-algebra's own any-join -- the
    # first satisfied condition stops the run.
    orch = open_channel()
    orch.send({"from": {"step": 5}}, topic="control.stop", request_id="s1")
    orch.send({"from": {"step": 10}}, topic="control.stop", request_id="s2")
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.tick(step=3) is False
    assert w.tick(step=5) is True  #  s1 fires; s2's later arrival didn't erase it


def test_resumed_episode_ignores_prior_episodes_stop(open_channel):
    """Every control fact is live until its counter-record (the discharge rule,
    specs/stop-discharge.md), and a ``control.stop``'s counter-record is the
    next ``lifecycle.stopped``. Episode 1 honored this stop, so its ``stopped``
    discharged it; episode 2 -- resumed on the SAME run -- must not re-honor it
    and die at its first step. (A non-time subscription follows the same rule
    with ``unsubscribe`` as the counter-record: none arrived here, so it
    correctly carries across episodes -- see
    test_run_episodes.test_relaunch_extends_one_series; a TIME-referencing
    subscription is additionally episode-scoped, specs/time-lease-boundary.md.)
    """
    orch = open_channel()
    orch.send(
        {}, topic="control.stop", request_id="s1"
    )  #  stop-now; lands at a low seq

    # episode 1: claims its episode, drains + honors the stop, ends (started…stopped)
    with Worker(open_channel(), now=lambda: 0.0) as w1:
        list(w1.steps(total=3))

    # episode 2: resumes on the same run. The prior episode's stop must not carry.
    with Worker(open_channel(), now=lambda: 0.0) as w2:
        resumed = list(w2.steps(start=5, total=10))

    assert resumed == [5, 6, 7, 8, 9]  #  a re-armed stop would leave just [5]


def test_stop_between_episodes_honored_exactly_once(open_channel):
    # specs/stop-discharge.md S2, "the blip": a stop sent while the run is down
    # is NOT dropped -- the next episode answers it at its first safe point --
    # and answered exactly once: the blip's own stopped discharges it, so the
    # episode after runs free. Loud and once; neither silent nor forever.
    orch = open_channel()
    with Worker(open_channel(), now=lambda: 0.0) as w1:  #     episode 1
        list(w1.steps(total=3))
    orch.send({}, topic="control.stop", request_id="s1")  #    the run is down
    with Worker(open_channel(), now=lambda: 0.0) as w2:  #     episode 2: the blip
        blip = list(w2.steps(start=3, total=10))
    assert blip == [3]  #                                      answered immediately...
    with Worker(open_channel(), now=lambda: 0.0) as w3:  #     episode 3
        resumed = list(w3.steps(start=4, total=8))
    assert resumed == [4, 5, 6, 7]  #                          ...and exactly once


def test_one_stopped_discharges_every_pending_stop(open_channel):
    # specs/stop-discharge.md S4, second half: the discharge is a broadcast
    # answer (matching the stopped record's own broadcast nature) -- the stop
    # that never fired must not survive its episode's stopped and haunt the
    # resume (per-id discharge was refuted A3).
    orch = open_channel()
    orch.send({"from": {"step": 5}}, topic="control.stop", request_id="s1")
    orch.send({"from": {"step": 10}}, topic="control.stop", request_id="s2")
    with Worker(open_channel(), now=lambda: 0.0) as w1:
        halted = list(w1.steps(total=20))
    assert halted == [0, 1, 2, 3, 4, 5]  #                     the any-join fired at 5
    with Worker(open_channel(), now=lambda: 0.0) as w2:
        resumed = list(w2.steps(start=6, total=10))
    assert resumed == [
        6,
        7,
        8,
        9,
    ]  #                          s2 didn't haunt the resume


def test_crashed_episodes_undischarged_stop_rearms_on_resume(open_channel):
    # specs/stop-discharge.md crash edge: a stop arrived during an episode that
    # then crashed (no lifecycle.stopped) before the trigger. No counter-record
    # followed, so the stop is still pending -- the resumed episode re-arms and
    # honors it. (Green before and after the discharge fold: it pins that the
    # discharge boundary is the last *stopped*, not a registered-watermark --
    # the refuted A5 -- which would silently drop the unanswered command.)
    orch = open_channel()
    import socket

    orch.send(
        {"handle": f"local://{socket.gethostname()}/2147483646", "t": 0.0},
        topic="lifecycle.started",
    )  #  crashed: dead pid (THIS host), no stopped
    orch.send({"from": {"step": 8}}, topic="control.stop", request_id="s1")
    with Worker(open_channel(), now=lambda: 0.0) as w:
        assert (
            w.claimed is True
        )  #                            dead episode -> the claim is free
        resumed = list(w.steps(start=5, total=20))
    assert resumed == [
        5,
        6,
        7,
        8,
    ]  #                          re-armed; honored at its step


def test_stopped_emits_dying_breath(open_channel):
    w = Worker(open_channel(), now=lambda: 0.0)
    w.stopped(completed=True, final_step=500)
    e = open_channel().latest("lifecycle.stopped")
    assert e.body == {"completed": True, "error": None, "final_step": 500, "t": 0.0}
    assert e.request_id is None  # broadcast — every observer sees it


def test_stopped_with_error(open_channel):
    w = Worker(open_channel(), now=lambda: 0.0)
    w.stopped(error="boom")
    assert open_channel().latest("lifecycle.stopped").body == {
        "completed": False,
        "error": "boom",
        "final_step": None,
        "t": 0.0,
    }


def test_tick_emits_heartbeat_with_step_and_consumed_seq(open_channel):
    orch = open_channel()
    sub_seq = orch.send({}, topic="control.subscribe", name="loss", request_id="r1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=7)
    hb = open_channel().latest("lifecycle.heartbeat")
    # consumed_seq is the worker's read position in the inbound control order:
    # after draining, it has processed the subscribe at sub_seq.
    assert hb.body == {"step": 7, "consumed_seq": sub_seq, "t": 0.0}
    assert hb.request_id is None


def test_consumed_seq_advances_only_after_draining(open_channel):
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=0)  # nothing to drain yet
    assert open_channel().latest("lifecycle.heartbeat").body["consumed_seq"] == 0


def test_nak_when_until_already_satisfied(open_channel):
    orch = open_channel()
    orch.send(
        {"until": {"step": 50}}, topic="control.subscribe", name="loss", request_id="r1"
    )
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
    orch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="ok"
    )
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=0)  # must not raise
    naks = open_channel().read(topics=["lifecycle.nak"], request_ids=["bad"])
    assert naks[0].body["reason"] == "malformed"
    # the well-formed sibling still gets serviced -- one bad request isn't fatal
    assert open_channel().latest("value", "loss").request_id == "ok"


def test_nak_malformed_stop_from_does_not_crash(open_channel):
    # a malformed `from` on a stop must nak (like a malformed subscribe), not
    # crash the worker -- and must not poison the pending set and re-crash every tick.
    orch = open_channel()
    orch.send({"from": {"step": "oops"}}, topic="control.stop", request_id="s1")
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.tick(step=0) is False  # survives, does not stop
    assert w.tick(step=1) is False  # not poisoned
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
    assert w.tick(step=0) is False
    assert w.tick(step=2) is True
    assert open_channel().latest("lifecycle.nak") is None


def test_nak_stop_with_every_or_until(open_channel):
    orch = open_channel()
    # a stop is one-shot; every/until are rejected, not silently honored
    orch.send({"until": {"step": 999}}, topic="control.stop", request_id="s1")
    w = Worker(open_channel(), now=lambda: 0.0)
    result = w.tick(step=0)  # must NOT stop on a malformed stop
    assert result is False
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
    orch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss"
    )  # no request_id
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=0)
    nak = open_channel().latest("lifecycle.nak")
    assert nak.body["reason"] == "malformed"
    assert open_channel().read(topics=["value"]) == []  # nothing registered/emitted


# ----- the structural gate: full grammar validation of inbound control -----


def test_nak_misspelled_schedule_key(open_channel):
    # a misspelled slot is a sender bug (design §6 `malformed`), refused with a
    # reason naming the key -- NOT silently registered as a fire-once-now.
    orch = open_channel()
    orch.send(
        {"frm": {"step": 5}}, topic="control.subscribe", name="loss", request_id="r1"
    )
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=10)
    nak = open_channel().latest("lifecycle.nak")
    assert nak.request_id == "r1"
    assert nak.body["reason"] == "malformed"
    assert "frm" in nak.body["message"]
    assert open_channel().read(topics=["value"]) == []  # did not register or fire


def test_nak_null_schedule_slot(open_channel):
    # the schema has no null conditions: absent means absent (present-nullable
    # is a body-field convention, not a condition-slot one).
    orch = open_channel()
    orch.send({"from": None}, topic="control.subscribe", name="loss", request_id="r1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=0)
    nak = open_channel().latest("lifecycle.nak")
    assert nak.request_id == "r1"
    assert nak.body["reason"] == "malformed"


def test_nak_unknown_condition_atom(open_channel):
    orch = open_channel()
    orch.send(
        {"from": {"frobnicate": 1}},
        topic="control.subscribe",
        name="loss",
        request_id="r1",
    )
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=0)
    nak = open_channel().latest("lifecycle.nak")
    assert nak.request_id == "r1"
    assert nak.body["reason"] == "malformed"
    assert open_channel().read(topics=["value"]) == []


def test_nak_empty_any_or_all(open_channel):
    # minItems 1: an empty any is vacuously false and an empty all vacuously
    # true -- both are sender bugs, not degenerate schedules.
    orch = open_channel()
    orch.send(
        {"every": {"any": []}}, topic="control.subscribe", name="loss", request_id="r1"
    )
    orch.send(
        {"every": {"all": []}}, topic="control.subscribe", name="loss", request_id="r2"
    )
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=0)
    naks = open_channel().read(topics=["lifecycle.nak"])
    assert [(n.request_id, n.body["reason"]) for n in naks] == [
        ("r1", "malformed"),
        ("r2", "malformed"),
    ]
    assert open_channel().read(topics=["value"]) == []


def test_nak_bool_posing_as_number(open_channel):
    # Python True serializes to JSON true, which the schema rejects where it
    # wants an integer -- the validator must agree, not let bool ride int.
    orch = open_channel()
    orch.send(
        {"from": {"step": True}},
        topic="control.subscribe",
        name="loss",
        request_id="r1",
    )
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=5)
    assert open_channel().latest("lifecycle.nak").body["reason"] == "malformed"


def test_nak_negative_threshold(open_channel):
    orch = open_channel()
    orch.send(
        {"from": {"step": -1}}, topic="control.subscribe", name="loss", request_id="r1"
    )
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=0)
    assert open_channel().latest("lifecycle.nak").body["reason"] == "malformed"


def test_nak_stop_with_unknown_key(open_channel):
    # previously a bogus-key stop fell through to from_=None = stop-now; the
    # structural gate refuses it instead of honoring a request it can't read.
    orch = open_channel()
    orch.send({"bogus": 1}, topic="control.stop", request_id="s1")
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.tick(step=0) is False  # must NOT stop on a malformed stop
    nak = open_channel().latest("lifecycle.nak")
    assert nak.request_id == "s1"
    assert nak.body["reason"] == "malformed"


def test_valid_full_schedule_still_registers_and_serves(open_channel):
    # the gate must pass the whole legal grammar: all three slots, nested
    # any/all, and a count atom inside `until` (where it is grammatical).
    orch = open_channel()
    orch.send(
        {
            "from": {"step": 1},
            "every": {"any": [{"step": 2}, {"time_seconds": 60.0}]},
            "until": {"all": [{"step": 100}, {"count": 3}]},
        },
        topic="control.subscribe",
        name="loss",
        request_id="r1",
    )
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 0.5)
    w.tick(step=1)
    assert open_channel().latest("lifecycle.nak") is None
    assert open_channel().latest("value", "loss").request_id == "r1"


def test_worker_keeps_serving_after_a_malformed_request(open_channel):
    # one naked request is never fatal: the good sibling registers and serves.
    orch = open_channel()
    orch.send(
        {"frm": {"step": 1}}, topic="control.subscribe", name="loss", request_id="bad"
    )
    orch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="ok"
    )
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=0)
    naks = open_channel().read(topics=["lifecycle.nak"], request_ids=["bad"])
    assert naks[0].body["reason"] == "malformed"
    assert open_channel().latest("value", "loss").request_id == "ok"


def test_unserializable_value_fails_clearly_naming_the_metric(tmp_path):
    from runstate.channel import open_channel as oc

    ch = oc("r", root=tmp_path, backend="sqlite")  # default json_default=None
    ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="o"
    )
    w = Worker(ch, now=lambda: 0.0)
    w.set("loss", {1, 2})  # a set isn't JSON-serializable
    with pytest.raises(TypeError, match="'loss'.*not.*JSON-serializable"):
        w.tick(step=0)


def test_json_default_hook_coerces_exotic_values(tmp_path):
    from runstate.channel import open_channel as oc

    # the sender-side hook coerces a set -> sorted list so it round-trips
    ch = oc("r", root=tmp_path, backend="sqlite", json_default=sorted)
    ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="o"
    )
    w = Worker(ch, now=lambda: 0.0)
    w.set("loss", {3, 1, 2})
    w.tick(step=0)
    assert ch.latest("value", "loss").body["value"] == [1, 2, 3]


def test_nak_step_condition_on_stepless_worker(open_channel):
    orch = open_channel()
    orch.send(
        {"from": {"step": 100}}, topic="control.subscribe", name="loss", request_id="r1"
    )
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=None)  # stepless: a step threshold can never be satisfied
    assert open_channel().latest("lifecycle.nak").request_id == "r1"


def test_no_nak_for_a_future_step_on_a_stepped_worker(open_channel):
    orch = open_channel()
    orch.send(
        {"from": {"step": 100}}, topic="control.subscribe", name="loss", request_id="r1"
    )
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=50)  # not there yet — must NOT nak; it just waits
    assert open_channel().latest("lifecycle.nak") is None


def test_already_past_step_fires_at_current_step(open_channel):
    # the agreed clean >= semantics: subscribing past the threshold fires now,
    # at the current step (not a nak).
    orch = open_channel()
    orch.send(
        {"from": {"step": 100}}, topic="control.subscribe", name="loss", request_id="r1"
    )
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 0.5)
    w.tick(step=150)
    assert open_channel().latest("value", "loss").body == {
        "value": 0.5,
        "step": 150,
        "t": 0.0,
    }
    assert open_channel().latest("lifecycle.nak") is None


def test_constructing_a_worker_emits_started_with_a_handle(open_channel):
    w = Worker(open_channel(), now=lambda: 0.0)
    e = open_channel().latest("lifecycle.started")
    assert e is not None
    assert e.body["handle"].startswith("local://")  # self-reported liveness handle
    assert e.body["t"] == 0.0
    assert e.request_id is None


def test_steps_drives_ticks_default_preempted(open_channel):
    # falling off the loop with no explicit claim -> default preempted
    orch = open_channel()
    orch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r1"
    )
    with Worker(open_channel(), now=lambda: 0.0) as w:
        for step in w.steps(3):
            w.set("loss", float(step))
    obs = open_channel()
    assert [v.body for v in obs.read(topics=["value"])] == [
        {"value": 0.0, "step": 0, "t": 0.0},
        {"value": 1.0, "step": 1, "t": 0.0},
        {"value": 2.0, "step": 2, "t": 0.0},
    ]
    assert obs.latest("lifecycle.stopped").body == {
        "completed": False,
        "error": None,
        "final_step": 2,
        "t": 0.0,
    }


def test_steps_drives_ticks_explicit_completed_claim(open_channel):
    # worker explicitly claims completed=True before exiting its with block;
    # the __exit__ idempotency means the explicit stopped() wins (first writer).
    orch = open_channel()
    orch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r1"
    )
    with Worker(open_channel(), now=lambda: 0.0) as w:
        for step in w.steps(3):
            w.set("loss", float(step))
        w.stopped(completed=True)
    obs = open_channel()
    body = obs.latest("lifecycle.stopped").body
    assert body["completed"] is True
    assert body["error"] is None
    assert (
        body["final_step"] == 2
    )  #  stopped() auto-fills final_step from the last yielded step
    # only one stopped record: first writer wins
    assert len(obs.read(topics=["lifecycle.stopped"])) == 1


def test_steps_breaks_on_commanded_stop(open_channel):
    orch = open_channel()
    orch.send({"from": {"step": 2}}, topic="control.stop", request_id="s1")
    seen = []
    with Worker(open_channel(), now=lambda: 0.0) as w:
        for step in w.steps(10):
            seen.append(step)
    assert seen == [0, 1, 2]  # stops at the commanded step, never reaches 3..9
    assert open_channel().latest("lifecycle.stopped").body == {
        "completed": False,
        "error": None,
        "final_step": 2,
        "t": 0.0,
    }


def test_context_manager_reports_errored_on_exception(open_channel):
    with pytest.raises(ValueError):
        with Worker(open_channel(), now=lambda: 0.0) as w:
            for step in w.steps(10):
                if step == 1:
                    raise ValueError("boom")
    e = open_channel().latest("lifecycle.stopped")
    assert e.body == {"completed": False, "error": "boom", "final_step": 1, "t": 0.0}


def test_stopped_is_idempotent(open_channel):
    w = Worker(open_channel(), now=lambda: 0.0)
    w.stopped(completed=True)
    w.stopped(error="x")  # second call is a no-op
    stops = open_channel().read(topics=["lifecycle.stopped"])
    assert len(stops) == 1 and stops[0].body == {
        "completed": True,
        "error": None,
        "final_step": None,
        "t": 0.0,
    }


def test_second_worker_loses_the_claim_and_does_no_work(open_channel):
    ch = open_channel()
    # a live episode already exists: a started by *our* pid (resolves alive), no stopped
    ch.send({"handle": local_handle(), "t": 0.0}, topic="lifecycle.started")
    with Worker(open_channel(), now=lambda: 0.0) as w:
        assert (
            w.claimed is False
        )  #                           lost: an episode is already live
        for step in w.steps(total=3):
            w.set("loss", float(step))  #                      body must not run
    assert (
        open_channel().read(topics=["value"]) == []
    )  #      the loser emitted no values
    assert (
        len(open_channel().read(topics=["lifecycle.started"])) == 1
    )  # no second started


def test_claim_losers_bare_tick_appends_nothing(open_channel):
    # The callback-guest pattern drives bare tick(); a racing loser must not
    # drain, nak, or beacon onto the winner's live log ("explicit calls
    # included"). Its tick answers True -- stop at this safe point; `claimed`
    # says why.
    winner = Worker(open_channel(), now=lambda: 0.0)
    assert winner.claimed is True
    loser = Worker(open_channel(), now=lambda: 0.0)
    assert loser.claimed is False
    log_before = [e.seq for e in open_channel().read()]
    loser.set("loss", 1.0)  #                      local-only: allowed
    assert loser.tick(step=0) is True  #           the muzzle: touch nothing, stop now
    assert loser.stop_pending is True  #           the side-effect-free poll agrees
    assert [e.seq for e in open_channel().read()] == log_before


def test_steps_resumes_at_start_with_run_absolute_step(open_channel):
    orch = open_channel()
    orch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r"
    )
    with Worker(open_channel(), now=lambda: 0.0) as w:
        for step in w.steps(start=5, total=8):
            w.set("loss", float(step))
    steps = [v.body["step"] for v in open_channel().read(topics=["value"])]
    assert steps == [
        5,
        6,
        7,
    ]  #                                  run-absolute, not 0,1,2
    assert open_channel().latest("lifecycle.stopped").body["final_step"] == 7


def test_value_t_is_absolute_wall_clock_not_birth_relative(open_channel):
    orch = open_channel()
    orch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r"
    )
    with Worker(open_channel(), now=lambda: 1000.0) as w:
        for step in w.steps(total=2):
            w.set("loss", float(step))
    ts = [v.body["t"] for v in open_channel().read(topics=["value"])]
    assert ts == [1000.0, 1000.0]  #  absolute clock; birth-relative would be 0.0


# ----- emit: the unconditional broadcast-value verb (worker-chosen cadence) -----


def test_emit_logs_the_broadcast_point_now(open_channel):
    # {value, step: current, t: now}, request_id=None -- visible to the
    # log-as-cache plane (history) with no subscription anywhere.
    from runstate.memoizer import history

    ch = open_channel()
    with Worker(ch, now=lambda: 42.0) as w:
        for step in w.steps(total=3):
            w.emit("loss", float(step))
    evs = open_channel().read(topics=["value"])
    assert [(e.name, e.request_id) for e in evs] == [("loss", None)] * 3
    assert [e.body for e in evs] == [
        {"value": 0.0, "step": 0, "t": 42.0},
        {"value": 1.0, "step": 1, "t": 42.0},
        {"value": 2.0, "step": 2, "t": 42.0},
    ]
    assert [
        b["value"] for b in history(open_channel(), "loss", {"every": {"step": 1}})
    ] == [0.0, 1.0, 2.0]


def test_emit_updates_the_register_a_subscription_samples(open_channel):
    # register coherence: a worker using ONLY emit never leaves a subscriber
    # sampling an empty register -- the two planes can't disagree.
    orch = open_channel()
    orch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="obs"
    )
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=0)  #                      a safe point, as inside steps()'s body
    w.emit("loss", 0.7)
    w.tick(step=1)  #                      the subscription fires, samples the register
    samples = [
        e for e in open_channel().read(topics=["value"]) if e.request_id == "obs"
    ]
    assert samples and samples[-1].body["value"] == 0.7


def test_emit_is_muzzled_for_a_claim_loser(open_channel):
    ch = open_channel()
    winner = Worker(ch, now=lambda: 0.0)
    loser = Worker(open_channel(), now=lambda: 0.0)
    assert winner.claimed and not loser.claimed
    before = ch.last_seq()
    loser.emit("loss", 1.0)  #             silent no-op: a loser may not act
    assert ch.last_seq() == before


def test_emit_with_no_step_to_stamp_raises(open_channel):
    # before the first tick / on a stepless worker a step=None point would
    # permanently poison history() for the name (append-only log): refuse
    # loudly; raw channel.send is the stepless / caller-clocked path.
    w = Worker(open_channel(), now=lambda: 0.0)
    with pytest.raises(ValueError, match="poison"):
        w.emit("loss", 1.0)
    w.tick(step=None)  #                   the stepless (serve) path
    with pytest.raises(ValueError, match="poison"):
        w.emit("loss", 1.0)


def test_emit_unserializable_value_raises_naming_the_metric(open_channel):
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=0)
    with pytest.raises(TypeError, match="loss"):
        w.emit("loss", object())
