"""Schema conformance of what the staged live layer writes (docs/specs/commits-and-lineage.md
§3, §10, "The schemas"; plan Task 16).

Two guarantees, ported from ``tests/test_schema.py`` to log format 0.4.0:

1. Every record the reference implementation writes validates against the 0.4.0
   stack: the envelope schema and its topic's convention (``STACK_0_4_0``). A
   scenario drives the staged Worker, the launchers and the death-report helper
   through every reserved topic, harvests the logs, and validates each record; the
   topics seen must be ``Topic``, or "everything validated" is hollow.
2. The constraints are load-bearing. The 0.4.0 negative cases of §10 are
   ``tests/test_schema_v0_4_0.py``'s (``test_malformed_record_is_refused``,
   ``test_threshold_refuses_n_outside_the_range``); the 0.3.0 constraints its table
   does not carry are ported here, in 0.4.0 terms.
"""

import sys
from dataclasses import asdict

import pytest

pytest.importorskip("jsonschema")

from runstate.checkpoint import NoSave  # noqa: E402
from runstate.launcher import (  # noqa: E402
    Command,
    LocalLauncher,
    ThreadLauncher,
    ThreadTarget,
    report_death,
)
from runstate.vocabulary.coordinate import Commits, Target  # noqa: E402
from runstate.vocabulary.payloads import (  # noqa: E402
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
from runstate.worker import Worker  # noqa: E402
from tests.test_schema_v0_4_0 import STACK_0_4_0, rec, record_errors  # noqa: E402


def _record(e):
    return {
        "seq": e.seq,
        "topic": e.topic,
        "name": e.name,
        "request_id": e.request_id,
        "body": e.body,
    }


def _thread(fn):
    return ThreadTarget(fn, args=(), kwargs={})


def _send(ch, topic, body, *, name=None, request_id=None):
    ch.send(body, topic=topic, name=name, request_id=request_id)


# ----- the scenario -------------------------------------------------------------------


def _main(ch):
    """Emits, sets, commits, heartbeats, rewinds once, and exits preempted when the
    pre-staged stop's ``from`` holds, honoring it."""
    with Worker(ch, resume_from=None, sources=["lr"], save=NoSave()) as w:
        w.set("lr", 0.1)
        rewound = False
        for i in w.steps(None):
            w.emit("loss", 1.0 / (i + 1))
            w.set("lr", 0.1 / (i + 1))
            if i == 1:
                w.heartbeat()
            if i == 2 and not rewound:
                rewound = True
                w.rewind(None)


def _errors(ch):
    with Worker(ch, resume_from=None, sources=[], save=NoSave()) as w:
        for _ in w.steps(Target(Commits(), 2)):
            raise RuntimeError("boom")


def _discards(ch):
    with Worker(ch, resume_from=None, sources=[], save=NoSave()) as w:
        w.commit()
        w.emit("loss", 0.5)
        w.discard()


def _completes(ch):
    with Worker(ch, resume_from=None, sources=[], save=NoSave()) as w:
        for i in w.steps(Target(Commits(), 2)):
            w.emit("loss", float(i))
        w.stopped(completed=True, error=None)


def test_the_scenario_emits_every_reserved_topic_and_each_record_conforms():
    """A ``ThreadLauncher`` run of the staged Worker, pre-staged with a source
    subscription, a malformed one the schema admits (a pure demand with an
    ``every``, which §3 refuses: ``nak``), a lease (``bound``), a pure demand, a
    stop with a ``from`` and a withdrawal; the target emits, sets, commits,
    heartbeats, rewinds and exits. A second run errors, a third discards and exits,
    a fourth completes, and ``report_death`` ends a fifth's held claim
    (``died``). Every record validates against ``STACK_0_4_0``, and the topics seen
    are ``Topic``."""
    launcher = ThreadLauncher()
    main = launcher.create_channel("main")
    _send(
        main,
        Topic.CONTROL_SUBSCRIBE,
        {"every": {"commits": 1}},
        name="lr",
        request_id="src",
    )
    # a pure demand with an every: the schema admits it, and §3 refuses it (nak)
    _send(main, Topic.CONTROL_SUBSCRIBE, {"every": {"commits": 1}}, request_id="bad")
    lease = {"every": {"commits": 2}, "until": {"time_seconds": 3600}}
    _send(main, Topic.CONTROL_SUBSCRIBE, lease, name="lr", request_id="lease")
    _send(main, Topic.CONTROL_SUBSCRIBE, {"until": {"commits": 100}}, request_id="pure")
    _send(
        main,
        Topic.CONTROL_SUBSCRIBE,
        {"every": {"commits": 1}},
        name="lr",
        request_id="gone",
    )
    _send(main, Topic.CONTROL_WITHDRAW, asdict(Withdraw(t=1.0)), request_id="gone")
    _send(main, Topic.CONTROL_STOP, {"from": {"commits": 3}}, request_id="halt")
    for run_id, fn in (
        ("main", _main),
        ("errors", _errors),
        ("discards", _discards),
        ("completes", _completes),
    ):
        launcher.launch(run_id, _thread(fn)).wait()

    reported = launcher.create_channel("reported")
    w = Worker(reported, resume_from=None, sources=[], save=NoSave())
    claim = reported.latest(Topic.LIFECYCLE_STARTED).seq
    assert report_death(
        reported, claim, outcome=Outcome.KILLED, evidence="slurm:TIMEOUT", force=True
    )
    assert w.commit() is True and w.released  # it reads the report and fences

    records = []
    for run_id in ("main", "errors", "discards", "completes", "reported"):
        records += launcher.attach_channel(run_id).read()
    bad = {
        (e.topic, e.seq): errs for e in records if (errs := record_errors(_record(e)))
    }
    assert bad == {}
    assert {e.topic for e in records} == set(Topic)
    naks = [e for e in records if e.topic == Topic.LIFECYCLE_NAK]
    assert [(e.request_id, e.body["reason"]) for e in naks] == [("bad", "malformed")]
    stops = [
        e.body
        for e in launcher.attach_channel("main").read(topics=[Topic.LIFECYCLE_STOPPED])
    ]
    assert [s["honored"] for s in stops] == [["halt"]]


# ----- what the scenario does not reach ------------------------------------------------


def test_an_emitted_killed_terminated_conforms(tmp_path):
    """The real reap path: a child killed by ``SIGTERM`` writes ``terminated`` with
    its signal."""
    with LocalLauncher(root=tmp_path) as launcher:
        cmd = Command([sys.executable, "-c", "import time; time.sleep(60)"], env={})
        h = launcher.launch("victim", cmd)
        h.terminate()
        h.wait()
    term = launcher.attach_channel("victim").latest(Topic.SUPERVISOR_TERMINATED)
    assert term.body["reason"] == "killed"
    assert record_errors(_record(term)) == []


def test_every_nak_reason_the_worker_writes_conforms(tmp_path):
    """The Worker's three refusals: a malformed schedule, an unsatisfiable one (a
    target that is no source), and an unknown control verb."""
    ch = ThreadLauncher(root=tmp_path).create_channel("naks")
    _send(
        ch, Topic.CONTROL_SUBSCRIBE, {"frm": {"commits": 1}}, name="lr", request_id="a"
    )
    _send(
        ch,
        Topic.CONTROL_SUBSCRIBE,
        {"every": {"commits": 1}},
        name="nope",
        request_id="b",
    )
    _send(ch, "control.frobnicate", {}, request_id="c")
    w = Worker(ch, resume_from=None, sources=["lr"], save=NoSave())
    w.commit()
    naks = ch.read(topics=[Topic.LIFECYCLE_NAK])
    assert {e.body["reason"] for e in naks} == set(NakReason)
    assert [record_errors(_record(e)) for e in naks] == [[], [], []]


def test_the_typed_bodies_serialize_to_schema_valid_records():
    """The frozen bodies mirror the schemas: each one's ``asdict``, under the
    envelope its topic requires, validates, so the two encodings cannot drift."""
    bodies = [
        (Value(value=0.5, t=0.0), {"name": "loss"}),
        (Value(value=0.5, t=None), {"name": "loss"}),
        (Started(handle="local://h/1", t=0.0, resume_from=None, sources=["lr"]), {}),
        (
            Commit(
                claim_seq=1,
                consumed_seq=0,
                parent=None,
                values=[2],
                fired=["r"],
                expired=[],
                t=0.0,
            ),
            {},
        ),
        (Heartbeat(claim_seq=1, consumed_seq=3, t=0.0), {}),
        (Rewind(claim_seq=1, resume_from=None, t=0.0), {}),
        (
            Stopped(
                completed=True,
                error=None,
                claim_seq=1,
                honored=["s"],
                parent=3,
                values=[],
                fired=[],
                expired=[],
                t=0.0,
            ),
            {},
        ),
        (Bound(claim_seq=1), {"request_id": "lease"}),
        (Nak(reason=NakReason.MALFORMED, message="x"), {"request_id": "r"}),
        (Withdraw(t=0.0), {"request_id": "r"}),
        (Launched(handle="local://h/1", status="running", t=0.0), {"request_id": "L1"}),
        (
            Terminated(reason=Reap.EXITED, exit_code=0, signal=None, t=0.0),
            {"request_id": "L1"},
        ),
        (
            Terminated(reason=Reap.KILLED, exit_code=None, signal=9, t=0.0),
            {"request_id": "L1"},
        ),
        (Died(claim_seq=1, outcome=Outcome.LOST, evidence="probe: x", t=0.0), {}),
    ]
    for body, envelope in bodies:
        assert record_errors(rec(type(body).TOPIC, asdict(body), **envelope)) == []


# ----- the envelope ------------------------------------------------------------------


def _envelope_errors(record):
    return list(STACK_0_4_0.envelope.iter_errors(record))


def test_the_envelope_is_closed_and_requires_seq_topic_and_body():
    assert _envelope_errors(rec("value", {"value": 1, "t": None}, name="x")) == []
    assert _envelope_errors({**rec("value", {}), "surprise": True})
    for missing in ("seq", "topic", "body"):
        record = rec("value", {})
        del record[missing]
        assert _envelope_errors(record), missing


def test_the_envelopes_structural_constraints():
    """Seqs start at 1, a topic is non-empty, a body is an object, and an id is never
    the empty string."""
    for bad in (
        {**rec("value", {}), "seq": 0},
        rec("", {}),
        {**rec("value", {}), "body": [1]},
        rec("value", {}, name=""),
        rec("value", {}, request_id=""),
    ):
        assert _envelope_errors(bad), bad


# ----- the 0.3.0 constraints test_schema_v0_4_0.py's table does not carry --------------

_HEARTBEAT = {"claim_seq": 1, "consumed_seq": 0, "t": 0.0}
_STOPPED = {
    "completed": False,
    "error": None,
    "claim_seq": 1,
    "honored": [],
    "parent": None,
    "values": [],
    "fired": [],
    "expired": [],
    "t": 0.0,
}
_STARTED = {"handle": "local://h/1", "t": 1.5, "resume_from": None, "sources": []}
_EXITED = {"reason": "exited", "exit_code": 0, "signal": None, "t": 0.0}
_KILLED = {"reason": "killed", "exit_code": None, "signal": 9, "t": 0.0}


def _without(body, key):
    return {k: v for k, v in body.items() if k != key}


REFUSED = {
    # value-v0.3
    "value with an extra field": rec(
        "value", {"value": 1, "t": 0.0, "oops": 1}, name="x"
    ),
    "value without its value": rec("value", {"t": 0.0}, name="x"),
    # lifecycle-v0.6: the heartbeat
    "heartbeat without consumed_seq": rec(
        "lifecycle.heartbeat", _without(_HEARTBEAT, "consumed_seq")
    ),
    "heartbeat with a negative watermark": rec(
        "lifecycle.heartbeat", {**_HEARTBEAT, "consumed_seq": -1}
    ),
    "heartbeat without claim_seq": rec(
        "lifecycle.heartbeat", _without(_HEARTBEAT, "claim_seq")
    ),
    "heartbeat with a null claim_seq": rec(
        "lifecycle.heartbeat", {**_HEARTBEAT, "claim_seq": None}
    ),
    "heartbeat with claim_seq 0": rec(
        "lifecycle.heartbeat", {**_HEARTBEAT, "claim_seq": 0}
    ),
    "heartbeat with an extra field": rec(
        "lifecycle.heartbeat", {**_HEARTBEAT, "extra": 1}
    ),
    # the exit: every field present, honored a set of non-empty ids
    **{
        f"stopped without {k}": rec("lifecycle.stopped", _without(_STOPPED, k))
        for k in ("error", "claim_seq", "honored", "values", "fired", "expired", "t")
    },
    "stopped honoring an id twice": rec(
        "lifecycle.stopped", {**_STOPPED, "honored": ["s", "s"]}
    ),
    "stopped honoring a non-string": rec(
        "lifecycle.stopped", {**_STOPPED, "honored": [1]}
    ),
    "stopped with a null honored": rec(
        "lifecycle.stopped", {**_STOPPED, "honored": None}
    ),
    "stopped with claim_seq 0": rec("lifecycle.stopped", {**_STOPPED, "claim_seq": 0}),
    # the claim: t required and non-null, handle required, hostname gone
    "started with a null t": rec("lifecycle.started", {**_STARTED, "t": None}),
    "started without t": rec("lifecycle.started", _without(_STARTED, "t")),
    "started with a hostname": rec("lifecycle.started", {**_STARTED, "hostname": None}),
    "started without handle": rec("lifecycle.started", _without(_STARTED, "handle")),
    # the refusal: a closed reason, a message
    "nak without message": rec(
        "lifecycle.nak", {"reason": "malformed"}, request_id="r"
    ),
    "nak without reason": rec("lifecycle.nak", {"message": "x"}, request_id="r"),
    "nak with an unknown reason": rec(
        "lifecycle.nak", {"reason": "whatever", "message": "x"}, request_id="r"
    ),
    # the binding: an id and a claim
    "bound without request_id": rec("lifecycle.bound", {"claim_seq": 3}),
    "bound without claim_seq": rec("lifecycle.bound", {}, request_id="lease"),
    "bound with a null claim_seq": rec(
        "lifecycle.bound", {"claim_seq": None}, request_id="lease"
    ),
    "bound with claim_seq 0": rec(
        "lifecycle.bound", {"claim_seq": 0}, request_id="lease"
    ),
    "bound with an extra field": rec(
        "lifecycle.bound", {"claim_seq": 1, "x": 1}, request_id="lease"
    ),
    # supervisor-v0.5
    "launched with an unknown status": rec(
        "supervisor.launched",
        {"handle": "local://h/1", "status": "intended", "t": 0.0},
        request_id="L1",
    ),
    "terminated without request_id": rec("supervisor.terminated", _EXITED),
    "terminated with a negative exit code": rec(
        "supervisor.terminated", {**_EXITED, "exit_code": -1}, request_id="L1"
    ),
    "terminated killed by signal 0": rec(
        "supervisor.terminated", {**_KILLED, "signal": 0}, request_id="L1"
    ),
    "terminated killed with an exit code": rec(
        "supervisor.terminated", {**_KILLED, "exit_code": 5}, request_id="L1"
    ),
    "terminated exited with a null exit code": rec(
        "supervisor.terminated", {**_EXITED, "exit_code": None}, request_id="L1"
    ),
    "terminated killed with a null signal": rec(
        "supervisor.terminated", {**_KILLED, "signal": None}, request_id="L1"
    ),
    "terminated without its signal key": rec(
        "supervisor.terminated", _without(_EXITED, "signal"), request_id="L1"
    ),
    "terminated without its exit_code key": rec(
        "supervisor.terminated", _without(_KILLED, "exit_code"), request_id="L1"
    ),
    "unknown supervisor topic": rec("supervisor.bogus", {}, request_id="L1"),
    # subscription-v0.4: the grammar and the ids
    "count nested in from": rec(
        "control.subscribe",
        {"from": {"any": [{"count": 3}, {"commits": 10}]}},
        name="x",
        request_id="r",
    ),
    "count in every": rec(
        "control.subscribe", {"every": {"count": 2}}, name="x", request_id="r"
    ),
    "unknown atom": rec(
        "control.subscribe", {"from": {"frobnicate": 1}}, name="x", request_id="r"
    ),
    "empty any": rec(
        "control.subscribe", {"every": {"any": []}}, name="x", request_id="r"
    ),
    "empty all": rec(
        "control.subscribe", {"every": {"all": []}}, name="x", request_id="r"
    ),
    "negative time_seconds": rec(
        "control.subscribe", {"from": {"time_seconds": -1}}, name="x", request_id="r"
    ),
    "unknown control topic": rec("control.bogus", {}, request_id="r"),
    "stop with an every": rec(
        "control.stop", {"every": {"commits": 1}}, request_id="s"
    ),
    "stop with an until": rec("control.stop", {"until": {"count": 5}}, request_id="s"),
    "subscribe without request_id": rec(
        "control.subscribe", {"every": {"commits": 1}}, name="x"
    ),
    "stop without request_id": rec("control.stop", {"from": {"commits": 1}}),
}


@pytest.mark.parametrize("label", sorted(REFUSED))
def test_a_ported_constraint_is_refused(label):
    assert record_errors(REFUSED[label]), label


ADMITTED = {
    "count nested in until": rec(
        "control.subscribe",
        {"until": {"any": [{"count": 3}, {"commits": 10}]}},
        name="x",
        request_id="r",
    ),
    "stop now": rec("control.stop", {}, request_id="s"),
    "stop at a count": rec("control.stop", {"from": {"commits": 100}}, request_id="s"),
    **{
        f"nak {reason}": rec(
            "lifecycle.nak", {"reason": reason, "message": "x"}, request_id="r"
        )
        for reason in NakReason
    },
    "terminated killed": rec("supervisor.terminated", _KILLED, request_id="L1"),
    "a hand-run worker's claim names no launch": rec("lifecycle.started", _STARTED),
}


@pytest.mark.parametrize("label", sorted(ADMITTED))
def test_a_ported_shape_is_admitted(label):
    assert record_errors(ADMITTED[label]) == [], label
