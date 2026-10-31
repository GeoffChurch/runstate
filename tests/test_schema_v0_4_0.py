"""The 0.4.0 convention schemas (docs/specs/commits-and-lineage.md section 3, 10).

They sit beside 0.3.0's in protocol/; tests/test_schema.py keeps validating
0.3.0. `STACK_0_4_0` and `row_errors` are what the migration's tests validate
migrated rows with.
"""

import json
from pathlib import Path
from typing import Any, NamedTuple

import pytest

jsonschema = pytest.importorskip("jsonschema")
from jsonschema import Draft202012Validator  # noqa: E402

_PROTO = Path(__file__).resolve().parent.parent / "protocol"


def _load(name: str) -> Draft202012Validator:
    schema = json.loads((_PROTO / f"{name}.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


class SchemaStack0_4_0(NamedTuple):
    """The validators of log format 0.4.0: the envelope, then each convention."""

    envelope: Draft202012Validator
    control: Draft202012Validator
    lifecycle: Draft202012Validator
    supervisor: Draft202012Validator
    value: Draft202012Validator


STACK_0_4_0 = SchemaStack0_4_0(
    envelope=_load("envelope-v0.2"),
    control=_load("subscription-v0.4"),
    lifecycle=_load("lifecycle-v0.6"),
    supervisor=_load("supervisor-v0.5"),
    value=_load("value-v0.3"),
)


def _convention(topic: str) -> Draft202012Validator | None:
    if topic.startswith("control."):
        return STACK_0_4_0.control
    if topic.startswith("lifecycle."):
        return STACK_0_4_0.lifecycle
    if topic.startswith("supervisor."):
        return STACK_0_4_0.supervisor
    if topic == "value":
        return STACK_0_4_0.value
    return None


def record_errors(record: dict[str, Any]) -> list[str]:
    """The envelope's and the topic's convention's validation errors for one
    record `{seq, topic, name, request_id, body}`; empty when it conforms."""
    convention = _convention(record["topic"])
    if convention is None:
        return [f"no 0.4.0 convention owns topic {record['topic']!r}"]
    return [
        e.message
        for v in (STACK_0_4_0.envelope, convention)
        for e in v.iter_errors(record)
    ]


def row_errors(row: Any) -> list[str]:
    """`record_errors` of a migration `Row` (its body a JSON string)."""
    return record_errors(
        {
            "seq": row.seq,
            "topic": row.topic,
            "name": row.name,
            "request_id": row.request_id,
            "body": json.loads(row.body),
        }
    )


def rec(topic, body, *, name=None, request_id=None, seq=10):
    return {
        "seq": seq,
        "topic": topic,
        "name": name,
        "request_id": request_id,
        "body": body,
    }


# ----- valid bodies, one per record of spec section 3 ------------------------------

_STOPPED = {
    "completed": False,
    "error": None,
    "claim_seq": 1,
    "honored": [],
    "parent": None,
    "values": [],
    "fired": [],
    "expired": [],
    "t": 1.0,
}


def _stopped(**kw):
    return {**_STOPPED, **kw}


VALID = {
    "commit": rec(
        "lifecycle.commit",
        {
            "claim_seq": 1,
            "consumed_seq": 0,
            "parent": None,
            "values": [2, 3],
            "fired": ["r"],
            "expired": [],
            "t": 1.0,
        },
    ),
    "stopped completed": rec(
        "lifecycle.stopped", _stopped(completed=True, parent=4, values=[5])
    ),
    "stopped preempted": rec("lifecycle.stopped", _stopped(honored=["s"], values=[5])),
    "stopped torn (discard)": rec("lifecycle.stopped", _stopped(values=None)),
    "stopped completed torn": rec(
        "lifecycle.stopped", _stopped(completed=True, values=None)
    ),
    "stopped errored": rec(
        "lifecycle.stopped", _stopped(error="boom", values=None, parent=3)
    ),
    "rewind": rec("lifecycle.rewind", {"claim_seq": 1, "resume_from": 4, "t": 1.0}),
    "rewind to root": rec(
        "lifecycle.rewind", {"claim_seq": 1, "resume_from": None, "t": 1.0}
    ),
    "started fresh": rec(
        "lifecycle.started",
        {"handle": "local://h/1", "t": 0.0, "resume_from": None, "sources": []},
    ),
    "started resumed": rec(
        "lifecycle.started",
        {"handle": "local://h/1", "t": 0.0, "resume_from": 7, "sources": ["loss"]},
        request_id="launch",
    ),
    "heartbeat": rec(
        "lifecycle.heartbeat", {"claim_seq": 1, "consumed_seq": 3, "t": 1.0}
    ),
    "bound": rec("lifecycle.bound", {"claim_seq": 1}, request_id="lease"),
    "nak": rec(
        "lifecycle.nak",
        {"reason": "malformed", "message": "bad"},
        request_id="r",
    ),
    "subscribe": rec(
        "control.subscribe",
        {"from": {"commits": 1}, "every": {"stream": "loss", "n": 2}},
        name="loss",
        request_id="r",
    ),
    "subscribe pure demand": rec(
        "control.subscribe", {"until": {"time_seconds": 5}}, request_id="r"
    ),
    "stop": rec("control.stop", {"from": {"commits": 3}}, request_id="s"),
    "stop now": rec("control.stop", {}, request_id="s"),
    "withdraw": rec("control.withdraw", {"t": 2.0}, request_id="r"),
    "launched": rec(
        "supervisor.launched",
        {"handle": "local://h/1", "status": "running", "t": 0.0},
        request_id="launch",
    ),
    "terminated exited": rec(
        "supervisor.terminated",
        {"reason": "exited", "exit_code": 0, "signal": None, "t": 1.0},
        request_id="launch",
    ),
    "terminated killed": rec(
        "supervisor.terminated",
        {"reason": "killed", "exit_code": None, "signal": 9, "t": 1.0},
        request_id="launch",
    ),
    "value": rec("value", {"value": {"x": [1, 2]}, "t": 1.5}, name="loss"),
    "value unstamped": rec("value", {"value": 0.5, "t": None}, name="loss"),
}
for _outcome in ("preempted", "errored", "killed", "lost"):
    VALID[f"died {_outcome}"] = rec(
        "supervisor.died",
        {"claim_seq": 1, "outcome": _outcome, "evidence": "slurm:TIMEOUT", "t": 1.0},
    )


@pytest.mark.parametrize(
    "schema", ["lifecycle-v0.6", "subscription-v0.4", "supervisor-v0.5", "value-v0.3"]
)
def test_each_schema_is_a_valid_schema(schema):
    Draft202012Validator.check_schema(
        json.loads((_PROTO / f"{schema}.schema.json").read_text())
    )


def test_protocol_holds_exactly_the_0_4_0_stack():
    """0.3.0's schemas left for the step tests' fixtures (tests/fixtures/log-format-0.3.0/)
    at the cutover; protocol/ holds the current format's stack and nothing else."""
    held = {p.name.removesuffix(".schema.json") for p in _PROTO.glob("*.schema.json")}
    assert held == {
        "envelope-v0.2",
        "lifecycle-v0.6",
        "subscription-v0.4",
        "supervisor-v0.5",
        "value-v0.3",
    }


@pytest.mark.parametrize("label", list(VALID))
def test_valid_record_conforms(label):
    assert record_errors(VALID[label]) == []


# ----- refusals -------------------------------------------------------------------


def _with(label, **body):
    r = VALID[label]
    return {**r, "body": {**r["body"], **body}}


NEGATIVE = {
    # spec section 10's list
    "error with non-null values": _with("stopped errored", values=[5]),
    "torn exit that fires": _with("stopped torn (discard)", fired=["r"]),
    "torn exit that expires": _with("stopped torn (discard)", expired=["r"]),
    "died with outcome completed": _with("died lost", outcome="completed"),
    "value with a step": _with("value", step=3),
    "commits atom of -1": rec(
        "control.subscribe", {"from": {"commits": -1}}, name="x", request_id="r"
    ),
    # field by field
    "completed with an error": _with("stopped completed", error="boom"),
    "stopped with final_step": _with("stopped preempted", final_step=3),
    "stopped with null claim_seq": _with("stopped preempted", claim_seq=None),
    "stopped missing parent": rec(
        "lifecycle.stopped", {k: v for k, v in _STOPPED.items() if k != "parent"}
    ),
    "heartbeat with a step": _with("heartbeat", step=1),
    "commit with duplicate values": _with("commit", values=[2, 2]),
    "commit with a zero value seq": _with("commit", values=[0]),
    "commit with a zero parent": _with("commit", parent=0),
    "commit with an empty fired name": _with("commit", fired=[""]),
    "commit without t": rec(
        "lifecycle.commit",
        {k: v for k, v in VALID["commit"]["body"].items() if k != "t"},
    ),
    "started without sources": rec(
        "lifecycle.started", {"handle": "h", "t": 0.0, "resume_from": None}
    ),
    "started with duplicate sources": _with("started fresh", sources=["a", "a"]),
    "started with an empty source": _with("started fresh", sources=[""]),
    "rewind without resume_from": rec("lifecycle.rewind", {"claim_seq": 1, "t": 1.0}),
    "started with a zero resume_from": _with("started resumed", resume_from=0),
    "rewind with a zero resume_from": _with("rewind", resume_from=0),
    "stopped with a zero value seq": _with("stopped preempted", values=[0]),
    "stopped honoring the empty id": _with("stopped preempted", honored=[""]),
    "commit with a name": {**VALID["commit"], "name": "x"},
    "commit with a request_id": {**VALID["commit"], "request_id": "r"},
    "unknown lifecycle topic": rec("lifecycle.expired", {}),
    "retired unsubscribe": rec("control.unsubscribe", {}, request_id="r"),
    "withdraw without request_id": rec("control.withdraw", {"t": 1.0}),
    "withdraw without t": rec("control.withdraw", {}, request_id="r"),
    "stop with a name": {**VALID["stop"], "name": "loss"},
    "step atom": rec(
        "control.subscribe", {"from": {"step": 1}}, name="x", request_id="r"
    ),
    "count outside until": rec(
        "control.subscribe", {"every": {"count": 1}}, name="x", request_id="r"
    ),
    "stream atom without n": rec(
        "control.subscribe", {"from": {"stream": "x"}}, name="x", request_id="r"
    ),
    "stream atom with an empty stream": rec(
        "control.subscribe", {"from": {"stream": "", "n": 1}}, name="x", request_id="r"
    ),
    "stream n above 2^53-1": rec(
        "control.subscribe",
        {"from": {"stream": "x", "n": 2**53}},
        name="x",
        request_id="r",
    ),
    "launcher prefix": rec(
        "launcher.launched",
        {"handle": "h", "status": "running", "t": 0.0},
        request_id="l",
    ),
    "launched without request_id": rec(
        "supervisor.launched", {"handle": "h", "status": "running", "t": 0.0}
    ),
    "died with a request_id": {**VALID["died lost"], "request_id": "launch"},
    "died with an empty evidence": _with("died lost", evidence=""),
    "died with claim_seq 0": _with("died lost", claim_seq=0),
    "died without t": rec(
        "supervisor.died", {"claim_seq": 1, "outcome": "lost", "evidence": "e"}
    ),
    "terminated exited with a signal": rec(
        "supervisor.terminated",
        {"reason": "exited", "exit_code": 0, "signal": 9, "t": 1.0},
        request_id="l",
    ),
    "value without a name": rec("value", {"value": 1, "t": 1.0}),
    "value with a request_id": {**VALID["value"], "request_id": "r"},
    "value without t": rec("value", {"value": 1}, name="x"),
}


@pytest.mark.parametrize("label", list(NEGATIVE))
def test_malformed_record_is_refused(label):
    assert record_errors(NEGATIVE[label]) != []


@pytest.mark.parametrize("n", [0, 1, 2**53 - 1])
@pytest.mark.parametrize("atom", ["stream", "commits"])
def test_threshold_takes_n_from_0_to_2_53_minus_1(atom, n):
    cond = {"stream": "x", "n": n} if atom == "stream" else {"commits": n}
    for slot in ("from", "every", "until"):
        r = rec("control.subscribe", {slot: cond}, name="x", request_id="r")
        assert record_errors(r) == []
    assert record_errors(rec("control.stop", {"from": cond}, request_id="s")) == []


@pytest.mark.parametrize("atom", ["stream", "commits"])
def test_threshold_refuses_n_outside_the_range(atom):
    for n in (-1, 2**53):
        cond = {"stream": "x", "n": n} if atom == "stream" else {"commits": n}
        r = rec("control.subscribe", {"from": cond}, name="x", request_id="r")
        assert record_errors(r) != []


def test_count_is_valid_in_until_and_nested():
    r = rec(
        "control.subscribe",
        {"until": {"any": [{"count": 2}, {"commits": 9}]}},
        name="x",
        request_id="r",
    )
    assert record_errors(r) == []


def test_row_errors_reads_a_migration_row():
    from runstate.migrations import Row

    ok = Row(
        1,
        "lifecycle.heartbeat",
        None,
        None,
        json.dumps({"claim_seq": 1, "consumed_seq": 0, "t": 0.0}),
        0.0,
    )
    bad = ok._replace(
        body=json.dumps({"claim_seq": 1, "consumed_seq": 0, "t": 0.0, "step": 1})
    )
    assert row_errors(ok) == []
    assert row_errors(bad) != []
