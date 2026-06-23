"""Schema conformance (docs/design-v0.2.md §10).

Two guarantees:
1. Every envelope the reference implementation emits validates against the
   envelope schema AND its convention schema. We drive a scenario that exercises
   *every* reserved topic, harvest the log, and validate each record.
2. The constraints are load-bearing: representative malformed bodies are
   rejected (additionalProperties, closed enums, count-only-in-until).
"""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")
from jsonschema import Draft202012Validator  # noqa: E402

from runstate import Topic  # noqa: E402
from runstate.launcher import ThreadLauncher  # noqa: E402
from runstate.worker import Worker  # noqa: E402

_PROTO = Path(__file__).resolve().parent.parent / "protocol"


def _validator(name):
    schema = json.loads((_PROTO / f"{name}-v0.2.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


ENVELOPE = _validator("envelope")
CONVENTIONS = {
    "control.": _validator("subscription"),
    "lifecycle.": _validator("lifecycle"),
    "launcher.": _validator("launcher"),
    "value": _validator("value"),
}

ALL_RESERVED_TOPICS = {
    "control.subscribe", "control.unsubscribe", "control.stop",
    "lifecycle.started", "lifecycle.heartbeat", "lifecycle.stopped", "lifecycle.nak",
    "launcher.launched", "launcher.terminated",
    "value",
}


def test_topic_enum_matches_reserved_set():
    """Topic is the single source of the reserved routing keys: it must equal the
    set the schema scenario emits (ALL_RESERVED_TOPICS), or the two drift."""
    assert set(map(str, Topic)) == ALL_RESERVED_TOPICS


def _convention_for(topic):
    for prefix, v in CONVENTIONS.items():
        if topic == prefix or topic.startswith(prefix):
            return v
    raise AssertionError(f"no convention schema for topic {topic!r}")


def _worker_main(channel):
    with Worker(channel) as w:
        for step in w.steps(total=10):
            w.set("loss", 1.0 / (step + 1))


def test_every_emitted_envelope_conforms(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    obs = launcher.open_channel("run")
    # pre-stage control so the worker drains it on its first tick:
    obs.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="ok")
    obs.send({"until": {"count": 0}}, topic="control.subscribe", name="loss", request_id="bad")  # -> nak
    obs.send({}, topic="control.subscribe", name="loss", request_id="once")  # one-shot ->
    # the worker writes its expiry counter-record (a WORKER-authored
    # control.unsubscribe -- specs/service-worker.md), validated like any other
    obs.send({}, topic="control.unsubscribe", request_id="gone")
    obs.send({"from": {"step": 2}}, topic="control.stop", request_id="stop")

    launcher.launch("run", _worker_main).wait()

    envelopes = obs.read()
    assert any(e.topic == "control.unsubscribe" and e.request_id == "once"
               for e in envelopes)   # the worker-written expiry record is on the log
    seen = set()
    for e in envelopes:
        record = asdict(e)
        ENVELOPE.validate(record)
        _convention_for(e.topic).validate(record)
        seen.add(e.topic)

    # the scenario must actually exercise the whole reserved vocabulary,
    # else "everything validated" is hollow
    assert seen == ALL_RESERVED_TOPICS


# ----- the constraints are load-bearing -----


def _env(topic, body, **extra):
    return {"seq": 1, "topic": topic, "name": None, "request_id": None, "body": body, **extra}


def test_envelope_rejects_unknown_top_level_field():
    with pytest.raises(jsonschema.ValidationError):
        ENVELOPE.validate(_env("value", {"value": 1}, surprise=True))


def test_lifecycle_stopped_rejects_extra_body_field():
    bad = _env("lifecycle.stopped", {"completed": True, "error": None, "final_step": None, "oops": 1})
    with pytest.raises(jsonschema.ValidationError):
        CONVENTIONS["lifecycle."].validate(bad)


# ----- present-nullable: incidentally-optional fields are required (nullable),
# never omittable (a uniform key set for consumers) -----


def test_value_step_is_present_nullable():
    V = CONVENTIONS["value"]
    V.validate(_env("value", {"value": 1, "step": 5, "t": 0.0}))
    V.validate(_env("value", {"value": 1, "step": None, "t": 0.0}))  # null when stepless
    with pytest.raises(jsonschema.ValidationError):
        V.validate(_env("value", {"value": 1}))  # step omitted -> rejected


def test_value_t_is_present_nullable():
    V = CONVENTIONS["value"]
    V.validate(_env("value", {"value": 1, "step": 0, "t": 2.5}))   # a stamped wall-clock value
    V.validate(_env("value", {"value": 1, "step": 0, "t": None}))  # unstamped (real-time axis off)
    with pytest.raises(jsonschema.ValidationError):
        V.validate(_env("value", {"value": 1, "step": 0}))  # t omitted -> rejected


def test_stopped_error_and_final_step_present_nullable():
    L = CONVENTIONS["lifecycle."]
    L.validate(_env("lifecycle.stopped",
                    {"completed": True, "error": None, "final_step": None}))
    L.validate(_env("lifecycle.stopped",
                    {"completed": False, "error": "boom", "final_step": 5}))
    for missing in ({"completed": True, "error": None},      # final_step omitted
                    {"completed": True, "final_step": None},  # error omitted
                    {"completed": True}):                     # both omitted
        with pytest.raises(jsonschema.ValidationError):
            L.validate(_env("lifecycle.stopped", missing))


def test_stopped_rejects_completed_with_error():
    # The if-then schema constraint: completed=true => error must be null
    L = CONVENTIONS["lifecycle."]
    with pytest.raises(jsonschema.ValidationError):
        L.validate(_env("lifecycle.stopped",
                        {"completed": True, "error": "x", "final_step": None}))


def test_started_hostname_and_attached_at_present_nullable():
    L = CONVENTIONS["lifecycle."]
    L.validate(_env("lifecycle.started",
                    {"handle": "local://h/1", "hostname": None, "attached_at": None}))
    L.validate(_env("lifecycle.started",
                    {"handle": "local://h/1", "hostname": "h", "attached_at": 1.5}))
    for missing in ({"handle": "local://h/1", "hostname": None},     # attached_at omitted
                    {"handle": "local://h/1", "attached_at": None},  # hostname omitted
                    {"handle": "local://h/1"}):                      # both omitted
        with pytest.raises(jsonschema.ValidationError):
            L.validate(_env("lifecycle.started", missing))


def test_convention_dataclasses_serialize_to_schema_valid_bodies():
    # the frozen body dataclasses are the Python mirror of the schemas; their
    # asdict() must validate, so the two encodings can't drift.
    from dataclasses import asdict

    from runstate.vocabulary import payloads

    bodies = [
        payloads.Value(value=0.5, step=10, t=0.0),
        payloads.Started(handle="local://h/1", hostname=None, attached_at=0.0),
        payloads.Heartbeat(step=7, consumed_seq=3),
        payloads.Stopped(completed=True, error=None, final_step=9),
        payloads.Nak(reason="malformed", message="x"),
        payloads.Launched(handle="local://h/1"),
        payloads.Terminated(reason="exited", exit_code=0, signal=None),
        payloads.Terminated(reason="killed", signal=9, exit_code=None),
    ]
    for body in bodies:
        topic = type(body).TOPIC
        _convention_for(topic).validate(_env(topic, asdict(body)))


def test_launcher_launched_rejects_unknown_status():
    bad = _env("launcher.launched", {"handle": "local://h/1", "status": "intended"})
    with pytest.raises(jsonschema.ValidationError):
        CONVENTIONS["launcher."].validate(bad)


def test_subscription_rejects_count_outside_until():
    # `count` is a fire budget, valid only in `until` (§6) -- not in `from`
    bad = _env("control.subscribe", {"from": {"count": 5}}, request_id="r")
    with pytest.raises(jsonschema.ValidationError):
        CONVENTIONS["control."].validate(bad)
    # but count IS allowed in until
    CONVENTIONS["control."].validate(
        _env("control.subscribe", {"until": {"count": 5}}, request_id="r")
    )


def test_lifecycle_rejects_unknown_subtopic():
    with pytest.raises(jsonschema.ValidationError):
        CONVENTIONS["lifecycle."].validate(_env("lifecycle.bogus", {}))


def test_control_stop_takes_only_from():
    C = CONVENTIONS["control."]
    C.validate(_env("control.stop", {}))  # stop now
    C.validate(_env("control.stop", {"from": {"step": 100}}))  # stop at step 100
    for bad in ({"every": {"step": 1}}, {"until": {"count": 5}}):
        with pytest.raises(jsonschema.ValidationError):
            C.validate(_env("control.stop", bad))


def test_well_known_body_shapes_validate():
    # positive coverage for shapes the emitted-bytes scenario doesn't reach
    CONVENTIONS["launcher."].validate(
        _env("launcher.terminated", {"reason": "killed", "signal": 9, "exit_code": None})
    )
    for reason in ("malformed", "unsatisfiable", "unsupported"):
        CONVENTIONS["lifecycle."].validate(
            _env("lifecycle.nak", {"reason": reason, "message": "x"}, request_id="r")
        )


def test_nak_reason_is_a_closed_enum():
    ok = _env("lifecycle.nak", {"reason": "unsupported", "message": "x"})
    CONVENTIONS["lifecycle."].validate(ok)
    bad = _env("lifecycle.nak", {"reason": "whatever", "message": "x"})
    with pytest.raises(jsonschema.ValidationError):
        CONVENTIONS["lifecycle."].validate(bad)


def test_envelope_rejects_empty_string_ids():
    for field in ("name", "request_id"):
        with pytest.raises(jsonschema.ValidationError):
            ENVELOPE.validate(_env("value", {"value": 1}, **{field: ""}))


def test_terminated_rejects_negative_exit_code():
    bad = _env("launcher.terminated", {"reason": "exited", "exit_code": -1, "signal": None})
    with pytest.raises(jsonschema.ValidationError):
        CONVENTIONS["launcher."].validate(bad)


def test_terminated_enforces_reason_field_pairing():
    L = CONVENTIONS["launcher."]
    # present-nullable + reason-coupled: every key present; the inapplicable one null
    L.validate(_env("launcher.terminated", {"reason": "exited", "exit_code": 0, "signal": None}))
    L.validate(_env("launcher.terminated", {"reason": "killed", "signal": 9, "exit_code": None}))
    for bad in (
        {"reason": "exited", "exit_code": 0, "signal": 9},        # exited: signal must be null
        {"reason": "killed", "signal": 9, "exit_code": 5},        # killed: exit_code must be null
        {"reason": "exited", "exit_code": None, "signal": None},  # exited needs a non-null exit_code
        {"reason": "killed", "exit_code": None, "signal": None},  # killed needs a non-null signal
        {"reason": "exited", "exit_code": 0},                     # signal key missing (not omittable)
        {"reason": "killed", "signal": 9},                        # exit_code key missing
    ):
        with pytest.raises(jsonschema.ValidationError):
            L.validate(_env("launcher.terminated", bad))


def test_subscribe_requires_request_id():
    schedule = {"every": {"step": 1}}
    # present -> ok
    CONVENTIONS["control."].validate(
        {"seq": 1, "topic": "control.subscribe", "name": "loss",
         "request_id": "r", "body": schedule}
    )
    # missing/null -> rejected (subscribe/unsubscribe are correlated ops)
    with pytest.raises(jsonschema.ValidationError):
        CONVENTIONS["control."].validate(
            {"seq": 1, "topic": "control.subscribe", "name": "loss",
             "request_id": None, "body": schedule}
        )
    # stop does NOT require it (and takes only `from`)
    CONVENTIONS["control."].validate(
        {"seq": 1, "topic": "control.stop", "name": None,
         "request_id": None, "body": {"from": {"step": 1}}}
    )
