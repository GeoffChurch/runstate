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
    obs.send({}, topic="control.unsubscribe", request_id="gone")
    obs.send({"from": {"step": 2}}, topic="control.stop", request_id="stop")

    launcher.launch("run", _worker_main).wait()

    envelopes = obs.read()
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
    bad = _env("lifecycle.stopped", {"reason": "completed", "oops": 1})
    with pytest.raises(jsonschema.ValidationError):
        CONVENTIONS["lifecycle."].validate(bad)


def test_launcher_launched_rejects_unknown_status():
    bad = _env("launcher.launched", {"handle": "local://h/1", "status": "intended"})
    with pytest.raises(jsonschema.ValidationError):
        CONVENTIONS["launcher."].validate(bad)


def test_subscription_rejects_count_outside_until():
    # `count` is a fire budget, valid only in `until` (§6) -- not in `from`
    bad = _env("control.subscribe", {"from": {"count": 5}})
    with pytest.raises(jsonschema.ValidationError):
        CONVENTIONS["control."].validate(bad)
    # but count IS allowed in until
    CONVENTIONS["control."].validate(_env("control.subscribe", {"until": {"count": 5}}))


def test_lifecycle_rejects_unknown_subtopic():
    with pytest.raises(jsonschema.ValidationError):
        CONVENTIONS["lifecycle."].validate(_env("lifecycle.bogus", {}))
