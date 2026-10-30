"""The 0.4.0 bodies, ``Topic`` and the coordinate types, staged
(docs/specs/commits-and-lineage.md §2, §3, §4; plan Task 4).

Each body is a frozen dataclass that parses with ``Cls(**body)`` exactly where its
lifecycle-v0.6, subscription-v0.4, supervisor-v0.5 or value-v0.3 definition admits
the body, and serializes with ``asdict``.
"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from runstate._next.vocabulary.coordinate import Commits, Target
from runstate._next.vocabulary.payloads import (
    Bound,
    Commit,
    Died,
    Heartbeat,
    Launched,
    Nak,
    NakReason,
    Outcome,
    Reap,
    Rewind,
    Started,
    Stopped,
    Terminated,
    Topic,
    Value,
    Withdraw,
)

_PROTO = Path(__file__).resolve().parent.parent / "protocol"

_SAMPLES = [
    Value(value=0.5, t=0.0),
    Value(value={"x": [1, 2]}, t=None),
    Started(handle="local://h/1", t=0.0, resume_from=None, sources=[]),
    Started(handle="local://h/1", t=1.5, resume_from=7, sources=["lr", "grad"]),
    Commit(
        claim_seq=1,
        consumed_seq=0,
        parent=None,
        values=[2, 3],
        fired=["r"],
        expired=[],
        t=1.0,
    ),
    Commit(
        claim_seq=1, consumed_seq=4, parent=5, values=[], fired=[], expired=["r"], t=2
    ),
    Heartbeat(claim_seq=1, consumed_seq=3, t=1.0),
    Rewind(claim_seq=1, resume_from=4, t=1.0),
    Rewind(claim_seq=1, resume_from=None, t=1.0),
    Stopped(
        completed=True,
        error=None,
        claim_seq=1,
        honored=[],
        parent=4,
        values=[5],
        fired=[],
        expired=[],
        t=1.0,
    ),
    Stopped(
        completed=False,
        error=None,
        claim_seq=1,
        honored=["s"],
        parent=4,
        values=[],
        fired=["r"],
        expired=["r"],
        t=1.0,
    ),
    Stopped(
        completed=False,
        error="boom",
        claim_seq=1,
        honored=[],
        parent=3,
        values=None,
        fired=[],
        expired=[],
        t=1.0,
    ),
    Bound(claim_seq=3),
    Nak(reason=NakReason.MALFORMED, message="bad request"),
    Nak(reason=NakReason.UNSATISFIABLE, message=""),
    Withdraw(t=2.0),
    Launched(handle="local://h/1", status="running", t=0.0),
    Terminated(reason=Reap.EXITED, exit_code=0, signal=None, t=0.0),
    Terminated(reason=Reap.KILLED, exit_code=None, signal=9, t=3.0),
    *[
        Died(claim_seq=1, outcome=o, evidence="slurm:TIMEOUT", t=1.0)
        for o in sorted(Outcome.deaths())
    ],
]


def _envelope(body: Any) -> dict[str, Any]:
    """A record of the body's topic, its envelope as the schema requires."""
    from test_schema_v0_4_0 import rec

    topic = body.TOPIC
    name = "loss" if topic == Topic.VALUE else None
    request_id = {
        Topic.LIFECYCLE_BOUND: "lease",
        Topic.LIFECYCLE_NAK: "r",
        Topic.CONTROL_WITHDRAW: "r",
        Topic.SUPERVISOR_LAUNCHED: "launch",
        Topic.SUPERVISOR_TERMINATED: "launch",
    }.get(topic)
    return rec(topic, asdict(body), name=name, request_id=request_id)


@pytest.mark.parametrize("body", _SAMPLES, ids=lambda b: type(b).__name__)
def test_every_body_round_trips_through_asdict_and_its_schema(body):
    """Serialize for the wire, parse back: the identical value object, and a record
    the 0.4.0 stack admits. Holds 0.3.0's test_body_roundtrips_through_its_dict."""
    pytest.importorskip("jsonschema")
    from test_schema_v0_4_0 import record_errors

    assert record_errors(_envelope(body)) == []
    assert type(body)(**asdict(body)) == body
    assert type(body)(**json.loads(json.dumps(asdict(body)))) == body


def test_a_strenum_field_serializes_as_its_string():
    assert json.dumps(asdict(Nak(reason=NakReason.UNSUPPORTED, message="m"))) == (
        '{"reason": "unsupported", "message": "m"}'
    )


def test_an_enum_field_parses_from_its_string_into_its_member():
    """``Cls(**body)`` takes the wire string and holds the member."""
    died = Died(claim_seq=1, outcome="lost", evidence="probe:x", t=0.0)
    assert died.outcome is Outcome.LOST
    nak = Nak(reason="malformed", message="m")  # type: ignore[arg-type]
    assert nak.reason is NakReason.MALFORMED
    reap = Terminated(reason="killed", exit_code=None, signal=9, t=0.0)
    assert reap.reason is Reap.KILLED


# ----- parses iff the schema admits -------------------------------------------------

_DEFINITIONS = {
    Topic.VALUE: ("value-v0.3", "Value", Value),
    Topic.LIFECYCLE_STARTED: ("lifecycle-v0.6", "Started", Started),
    Topic.LIFECYCLE_COMMIT: ("lifecycle-v0.6", "Commit", Commit),
    Topic.LIFECYCLE_HEARTBEAT: ("lifecycle-v0.6", "Heartbeat", Heartbeat),
    Topic.LIFECYCLE_REWIND: ("lifecycle-v0.6", "Rewind", Rewind),
    Topic.LIFECYCLE_STOPPED: ("lifecycle-v0.6", "Stopped", Stopped),
    Topic.LIFECYCLE_BOUND: ("lifecycle-v0.6", "Bound", Bound),
    Topic.LIFECYCLE_NAK: ("lifecycle-v0.6", "Nak", Nak),
    Topic.CONTROL_WITHDRAW: ("subscription-v0.4", "Withdraw", Withdraw),
    Topic.SUPERVISOR_LAUNCHED: ("supervisor-v0.5", "Launched", Launched),
    Topic.SUPERVISOR_TERMINATED: ("supervisor-v0.5", "Terminated", Terminated),
    Topic.SUPERVISOR_DIED: ("supervisor-v0.5", "Died", Died),
}


def test_every_body_has_its_topic_and_a_definition():
    """Every topic whose body is a dataclass is in the iff test below; the two
    whose bodies are schedules are the condition algebra's (schedule.py)."""
    assert {cls.TOPIC for _, _, cls in _DEFINITIONS.values()} == set(_DEFINITIONS)
    assert set(Topic) - set(_DEFINITIONS) == {
        Topic.CONTROL_SUBSCRIBE,
        Topic.CONTROL_STOP,
    }


def _edges():
    """Bodies one field off a valid one, at the edges the parse mirrors, and with a
    key added or the first removed."""
    off: dict[Topic, list[tuple[str, object]]] = {
        Topic.VALUE: [("t", -1.0), ("t", "x"), ("t", True), ("t", None), ("t", 3)],
        Topic.LIFECYCLE_STARTED: [
            ("handle", 5),
            ("handle", ""),
            ("t", -1.0),
            ("t", True),
            ("t", None),
            ("resume_from", True),
            ("resume_from", 0),
            ("resume_from", "7"),
            ("sources", "lr"),
            ("sources", ["lr", "lr"]),
            ("sources", [""]),
            ("sources", [3]),
            ("sources", None),
        ],
        Topic.LIFECYCLE_COMMIT: [
            ("claim_seq", True),
            ("claim_seq", None),
            ("claim_seq", 0),
            ("consumed_seq", -1),
            ("consumed_seq", True),
            ("consumed_seq", 0),
            ("parent", 0),
            ("parent", None),
            ("parent", False),
            ("values", "x"),
            ("values", [True]),
            ("values", None),
            ("values", []),
            ("fired", ["q", "q"]),
            ("fired", None),
            ("expired", [""]),
            ("t", "1"),
        ],
        Topic.LIFECYCLE_HEARTBEAT: [
            ("claim_seq", 0),
            ("claim_seq", None),
            ("consumed_seq", -1),
            ("consumed_seq", None),
            ("t", -0.5),
        ],
        Topic.LIFECYCLE_REWIND: [
            ("claim_seq", None),
            ("resume_from", -3),
            ("resume_from", None),
            ("t", None),
        ],
        Topic.LIFECYCLE_STOPPED: [
            ("completed", "yes"),
            ("completed", None),
            ("error", 5),
            ("claim_seq", True),
            ("claim_seq", None),
            ("honored", ["s", "s"]),
            ("honored", [""]),
            ("honored", None),
            ("parent", 0),
            ("values", [2, 2]),
            ("values", None),
            ("values", [0]),
            ("fired", [""]),
            ("expired", None),
            ("t", -1.0),
        ],
        Topic.LIFECYCLE_BOUND: [
            ("claim_seq", 0),
            ("claim_seq", True),
            ("claim_seq", None),
        ],
        Topic.LIFECYCLE_NAK: [
            ("reason", "rude"),
            ("reason", None),
            ("reason", "unsupported"),
            ("message", None),
            ("message", 3),
            ("message", ""),
        ],
        Topic.CONTROL_WITHDRAW: [("t", -1), ("t", None), ("t", True), ("t", 0)],
        Topic.SUPERVISOR_LAUNCHED: [
            ("handle", None),
            ("status", "stopped"),
            ("status", None),
            ("t", -1.0),
        ],
        Topic.SUPERVISOR_TERMINATED: [
            ("reason", "crashed"),
            ("reason", None),
            ("exit_code", -1),
            ("exit_code", None),
            ("exit_code", True),
            ("signal", 9),
            ("t", -1.0),
        ],
        Topic.SUPERVISOR_DIED: [
            ("claim_seq", 0),
            ("claim_seq", True),
            ("claim_seq", None),
            ("outcome", "completed"),
            ("outcome", "presumed_dead"),
            ("outcome", 3),
            ("evidence", ""),
            ("evidence", 5),
            ("t", -1.0),
            ("t", None),
        ],
    }
    bodies = {
        Topic.VALUE: {"value": 0.9, "t": 1.0},
        Topic.LIFECYCLE_STARTED: {
            "handle": "h",
            "t": 0.0,
            "resume_from": None,
            "sources": ["lr"],
        },
        Topic.LIFECYCLE_COMMIT: {
            "claim_seq": 1,
            "consumed_seq": 1,
            "parent": 2,
            "values": [3],
            "fired": ["q"],
            "expired": [],
            "t": 1.0,
        },
        Topic.LIFECYCLE_HEARTBEAT: {"claim_seq": 1, "consumed_seq": 0, "t": 1.0},
        Topic.LIFECYCLE_REWIND: {"claim_seq": 1, "resume_from": 2, "t": 1.0},
        Topic.LIFECYCLE_STOPPED: {
            "completed": False,
            "error": None,
            "claim_seq": 1,
            "honored": [],
            "parent": 2,
            "values": [3],
            "fired": [],
            "expired": [],
            "t": 1.0,
        },
        Topic.LIFECYCLE_BOUND: {"claim_seq": 1},
        Topic.LIFECYCLE_NAK: {"reason": "malformed", "message": "m"},
        Topic.CONTROL_WITHDRAW: {"t": 1.0},
        Topic.SUPERVISOR_LAUNCHED: {"handle": "h", "status": "running", "t": 0.0},
        Topic.SUPERVISOR_TERMINATED: {
            "reason": "exited",
            "exit_code": 0,
            "signal": None,
            "t": 1.0,
        },
        Topic.SUPERVISOR_DIED: {
            "claim_seq": 1,
            "outcome": "killed",
            "evidence": "e",
            "t": 1.0,
        },
    }
    assert set(off) == set(bodies) == set(_DEFINITIONS)
    for topic, changes in off.items():
        for field, v in changes:
            yield topic, {**bodies[topic], field: v}
        yield topic, {**bodies[topic], "extra": 1}
        yield topic, {k: v for k, v in list(bodies[topic].items())[1:]}
    torn = {**bodies[Topic.LIFECYCLE_STOPPED], "values": None}
    yield Topic.LIFECYCLE_STOPPED, {**torn, "completed": True}  # closed, not torn
    yield Topic.LIFECYCLE_STOPPED, {**torn, "error": "boom"}
    yield Topic.LIFECYCLE_STOPPED, {**torn, "completed": True, "error": "boom"}
    yield Topic.LIFECYCLE_STOPPED, {**torn, "expired": ["q"]}
    yield Topic.LIFECYCLE_STOPPED, {**torn, "fired": ["q"]}
    # a non-string error the coupling cannot refuse first: values is already null
    yield Topic.LIFECYCLE_STOPPED, {**torn, "error": 5}
    yield Topic.LIFECYCLE_STOPPED, {**bodies[Topic.LIFECYCLE_STOPPED], "error": "x"}
    by_signal = {"reason": "killed", "exit_code": None, "signal": 9, "t": 1.0}
    yield Topic.SUPERVISOR_TERMINATED, by_signal
    yield Topic.SUPERVISOR_TERMINATED, {**by_signal, "signal": 0}
    yield Topic.SUPERVISOR_TERMINATED, {**by_signal, "signal": None}
    yield Topic.SUPERVISOR_TERMINATED, {**by_signal, "signal": True}
    yield Topic.SUPERVISOR_TERMINATED, {**by_signal, "exit_code": 0}


def _bodies():
    from test_schema_v0_4_0 import NEGATIVE, VALID

    for r in [*VALID.values(), *NEGATIVE.values()]:
        if r["topic"] in _DEFINITIONS:
            yield Topic(r["topic"]), r["body"]
    yield from _edges()


def test_a_body_parses_if_and_only_if_its_schema_definition_admits_it():
    """One meaning of a well-formed body, the schema's, body by body. A record's
    envelope (its topic, name and request_id) is the envelope schema's and the
    convention's; this compares the body against its definition alone."""
    pytest.importorskip("jsonschema")
    from jsonschema import Draft202012Validator

    disagree = []
    seen = set()
    for topic, body in _bodies():
        schema, definition, cls = _DEFINITIONS[topic]
        defs = json.loads((_PROTO / f"{schema}.schema.json").read_text())["$defs"]
        admits = Draft202012Validator(
            {"$defs": defs, "$ref": f"#/$defs/{definition}"}
        ).is_valid(body)
        try:
            cls(**body)
            parses = True
        except (TypeError, ValueError):
            parses = False
        if parses != admits:
            disagree.append((topic, body, admits))
        seen.add((topic, admits))
    assert disagree == []
    # every body class was tried both ways
    assert seen == {(t, a) for t in _DEFINITIONS for a in (True, False)}


def test_a_whole_number_float_is_no_integer_here_though_json_schema_admits_it():
    """JSON Schema's ``integer`` admits 3.0; the bodies refuse it, as the
    migration step does. No writer of a 0.4.0 log writes one: Python's ``json``
    writes an int as an int."""
    with pytest.raises(ValueError):
        Bound(claim_seq=2.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Heartbeat(claim_seq=1, consumed_seq=3.0, t=0.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Terminated(reason=Reap.EXITED, exit_code=0.0, signal=None, t=0.0)


def test_nan_is_no_number_here_though_json_schema_admits_it():
    """NaN compares false with every bound, so JSON Schema's ``minimum: 0`` admits
    it; the bodies refuse it. No writer's clock is NaN."""
    pytest.importorskip("jsonschema")
    from jsonschema import Draft202012Validator

    nan = float("nan")
    assert Draft202012Validator({"type": "number", "minimum": 0}).is_valid(nan)
    for build in (
        lambda: Value(value=1, t=nan),
        lambda: Withdraw(t=nan),
        lambda: Heartbeat(claim_seq=1, consumed_seq=0, t=nan),
        lambda: Launched(handle="h", status="running", t=nan),
    ):
        with pytest.raises(ValueError):
            build()


def test_the_topic_enum_is_the_0_4_0_reserved_set():
    """``Topic`` is exactly the topics the 0.4.0 convention schemas pin: every
    ``enum`` or ``const`` the schemas give a record's ``topic``."""
    pinned: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            topic = node.get("topic")
            if isinstance(topic, dict):
                pinned.update(topic.get("enum", []))
                if "const" in topic:
                    pinned.add(topic["const"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for schema in ("lifecycle-v0.6", "subscription-v0.4", "supervisor-v0.5"):
        walk(json.loads((_PROTO / f"{schema}.schema.json").read_text()))
    walk(json.loads((_PROTO / "value-v0.3.schema.json").read_text()))
    assert set(Topic) == pinned


def test_a_node_is_a_commit_or_an_exit_and_its_kind_follows_from_its_body():
    """§2, "Nodes": closed iff a completed exit; torn iff an exit that did not
    complete and discarded its iteration; extendable otherwise. ``Commits()``
    counts commits, so an exit adds no iteration (§2, "The coordinate")."""
    commit = Commit(
        claim_seq=1, consumed_seq=0, parent=None, values=[2], fired=[], expired=[], t=0
    )

    def exit_(completed: bool, values: list[int] | None) -> Stopped:
        return Stopped(
            completed=completed,
            error=None,
            claim_seq=1,
            honored=[],
            parent=3,
            values=values,
            fired=[],
            expired=[],
            t=0.0,
        )

    kinds = {
        "commit": commit,
        "closed": exit_(True, [4]),
        "closed, discarded": exit_(True, None),
        "torn": exit_(False, None),
        "extendable exit": exit_(False, [4]),
    }
    assert {k: (n.closed, n.extendable) for k, n in kinds.items()} == {
        "commit": (False, True),
        "closed": (True, False),
        "closed, discarded": (True, False),
        "torn": (False, False),
        "extendable exit": (False, True),
    }
    assert [n.iterations for n in kinds.values()] == [1, 0, 0, 0, 0]
    assert [n.committed for n in kinds.values()] == [[2], [4], [], [], [4]]


def test_a_target_checks_its_count_once():
    """§4, ``Target(x, n)``: the first n of x, with x an eager stream name or
    ``Commits()`` and n >= 0, checked at construction; the wire spells it as an
    atom of subscription-v0.4, so n is at most 2^53 - 1 too."""
    for x, n in [
        ("loss", -1),
        ("", 1),
        ("loss", True),
        ("loss", 1.0),
        ("loss", 2**53),
        (3, 1),
        (None, 1),
        (Commits, 1),  # the class, not its instance
    ]:
        with pytest.raises(ValueError):
            Target(x, n)  # type: ignore[arg-type]
    assert Target(Commits(), 0).atom() == {"commits": 0}
    assert Target("loss", 5).atom() == {"stream": "loss", "n": 5}
    assert Target("loss", 2**53 - 1).n == 2**53 - 1


def test_commits_is_a_singleton_marker_no_stream_name_collides_with():
    assert Commits() == Commits()
    assert hash(Commits()) == hash(Commits())
    assert Commits() != "commits"
    assert Target(Commits(), 3) == Target(Commits(), 3)
    assert Target(Commits(), 3) != Target("commits", 3)


def test_outcome_failures_and_deaths():
    """A death is never a completion (supervisor-v0.5), and presumed_dead is the
    Watcher's inference, which no death report carries; the failures are the
    outcomes that are not a clean finish or a preemption."""
    assert Outcome.deaths() == {
        Outcome.PREEMPTED,
        Outcome.ERRORED,
        Outcome.KILLED,
        Outcome.LOST,
    }
    assert Outcome.failures() == {
        Outcome.ERRORED,
        Outcome.KILLED,
        Outcome.LOST,
        Outcome.PRESUMED_DEAD,
    }
    assert set(Outcome) == Outcome.deaths() | {Outcome.COMPLETED, Outcome.PRESUMED_DEAD}
    defs = json.loads((_PROTO / "supervisor-v0.5.schema.json").read_text())["$defs"]
    assert set(defs["Died"]["properties"]["outcome"]["enum"]) == Outcome.deaths()


def test_died_refuses_an_outcome_that_is_no_death():
    for outcome in (Outcome.COMPLETED, Outcome.PRESUMED_DEAD):
        with pytest.raises(ValueError):
            Died(claim_seq=1, outcome=outcome, evidence="e", t=0.0)


# ----- ported from tests/test_payloads.py (0.3.0) ------------------------------------


def test_terminated_coupling_rejects_illegal_states():
    # exited(exit_code) XOR killed(signal) -- illegal states unrepresentable
    Terminated(reason=Reap.EXITED, exit_code=0, signal=None, t=0.0)  # ok
    Terminated(reason=Reap.KILLED, signal=9, exit_code=None, t=0.0)  # ok
    for kwargs in (
        {"reason": "exited", "exit_code": 0, "signal": 9, "t": 0.0},
        {"reason": "killed", "signal": 9, "exit_code": 5, "t": 0.0},
        {"reason": "exited", "exit_code": None, "signal": None, "t": 0.0},
        {"reason": "killed", "exit_code": None, "signal": None, "t": 0.0},
        {"reason": "bogus", "exit_code": None, "signal": None, "t": 0.0},
    ):
        with pytest.raises(ValueError):
            Terminated(**kwargs)


def test_completed_with_error_rejected():
    # completed=True => error is None: the invariant is enforced in __post_init__
    with pytest.raises(ValueError):
        Stopped(
            completed=True,
            error="x",
            claim_seq=1,
            honored=[],
            parent=None,
            values=None,
            fired=[],
            expired=[],
            t=0.0,
        )


def test_error_with_values_and_a_discard_that_evaluates_are_rejected():
    """lifecycle-v0.6's two other ``if``/``then``s: an error exit discards its
    iteration, and an exit that discarded its iteration evaluates nothing."""
    base: dict[str, Any] = {
        "completed": False,
        "error": None,
        "claim_seq": 1,
        "honored": [],
        "parent": None,
        "values": None,
        "fired": [],
        "expired": [],
        "t": 0.0,
    }
    for bad in (
        {"error": "boom", "values": []},
        {"fired": ["r"]},
        {"expired": ["r"]},
        {"completed": True, "fired": ["r"]},
    ):
        with pytest.raises(ValueError):
            Stopped(**{**base, **bad})


def test_bound_and_stopped_serialize_to_their_wire_fields():
    assert asdict(Bound(claim_seq=3)) == {"claim_seq": 3}
    s = Stopped(
        completed=True,
        error=None,
        claim_seq=3,
        honored=["s1"],
        parent=None,
        values=[],
        fired=[],
        expired=[],
        t=1.0,
    )
    assert asdict(s)["honored"] == ["s1"]


def test_stopped_honored_must_be_a_list_of_strings():
    for bad in (None, [1], "s1", [""], ["s1", "s1"]):
        with pytest.raises(ValueError):
            Stopped(
                completed=False,
                error=None,
                claim_seq=1,
                honored=bad,  # type: ignore[arg-type]
                parent=None,
                values=[],
                fired=[],
                expired=[],
                t=0.0,
            )
