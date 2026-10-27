"""The 0.3.0 -> 0.4.0 step (commits-and-lineage.md §7; log-formats.md §6): what it
reads and refuses at the boundary, its liveness, its place in the chain, its
declarations (facts, projections, choosers, divergences), and its import rule.

The golden logs live in ``tests/migration_goldens.py``; their whole outputs, each
golden restated as (log, facts, choices) -> output, are pinned in
``tests/test_migration_goldens.py``, which states each golden's facts through its
simulator history."""

import dataclasses
import functools
import contextlib
import itertools
import json
import os
import re
import socket
from pathlib import Path

import pytest

import runstate.migrations.v0_3_0_to_v0_4_0_engine as E
from runstate import migrations
from runstate.channel.sqlite import SqliteChannel
from runstate.formats import FORMATS, DirectoryLayout
from runstate.migrations import RENUMBERING_FROM, STEPS, MigrationError, Row, migrate
from runstate.migrations.ambiguity import (
    REFUSE,
    Assumptions,
    Chosen,
    Entry,
    Limits,
    Refusal,
    Resolutions,
    parse_choices,
    resolutions,
)
from runstate.migrations.stores import SqliteStore
from runstate.migrations.v0_3_0_to_v0_4_0 import (
    END_TIME,
    FACTS,
    FEWEST,
    MOST,
    NEWEST,
    SPAN,
    UNSTATED,
    DatedBy,
    EndedAt,
    EndTime,
    Undated,
    V0_3_0_to_V0_4_0,
    _Malformed,
    _Stopped,
    parse,
)
import runstate.migrations.v0_3_0_to_v0_4_0 as STEP
import runstate.migrations.v0_3_0_to_v0_4_0_facts as V
from tests.migration_goldens import (
    BACKSTOP,
    DEAD,
    EXIT,
    GOLDEN,
    RECLAIMED,
    _beat,
    _dated,
    _launched,
    _release,
    _started,
    _stopped,
    _terminated,
    _value,
    r,
)

step = V0_3_0_to_V0_4_0()
LIMITS = Limits(cap=3000, budget=6_000_000, run_on=2)
NONE = parse_choices([step], assume=[], resolve=[], accept=[], limits=LIMITS)


def transform(rows):
    """The step under no stated facts: the boundary refuses before the engine."""
    return step.transform(rows, resolutions=resolutions(step, NONE), history=()).rows


class _Reference:
    """A test classifier: every claim the reference Worker's (``"ref"``), every
    stopped its own or a third party's (``"tp"``), every value any role."""

    def read(self, log):
        return self

    def started(self, seq):
        return frozenset({"ref"})

    def stopped(self, seq):
        acts = [V.Release(o) for o in V.Outcome] + [V.Note()]
        return frozenset({V.OwnStop(), *(V.ThirdStop("tp", a) for a in acts)})

    def value(self, seq):
        return frozenset({V.ValueRole(V.Role.PASS, False)})


REFERENCE = V.Configuration(
    {
        "ref": dataclasses.replace(
            STEP.REFERENCE.workers["reference"],
            final_step=frozenset(V.FinalStep),
            error_final_step=frozenset({V.FinalStep.TICKED}),
            watermark=frozenset(V.Watermark),
        )
    },
    {"tp": UNSTATED.third_parties["any"]},
    _Reference(),
)
"""The shipped preset (``STEP.REFERENCE``) widened for these tests' hand-written logs:
any ``final_step`` at an exit, the last tick at an error, any watermark, and any third
party; narrow enough that a small log follows at the cap."""


def under(configuration, **choosers):
    """Resolutions: ``configuration`` stated, each projection's chooser as given, else
    ``refuse``."""
    picked = {
        p.name: choosers.get(p.name.replace("-", "_"), Chosen(None, REFUSE.chooser))
        for p in step.PROJECTIONS
    }
    return Resolutions(Assumptions(configuration, "test", LIMITS), picked)


NOTES = REFERENCE._replace(
    workers={
        "ref": dataclasses.replace(
            REFERENCE.workers["ref"], takeover=frozenset(V.Takeover)
        )
    },
    third_parties={
        "tp": dataclasses.replace(
            UNSTATED.third_parties["any"],
            acts=frozenset(
                V.Acts(state, V.Act.NOTE)
                for state in (V.ClaimState.EXITED, V.ClaimState.ENDED)
            ),
        )
    },
)
"""The reference Worker, its third party writing only notes on ended claims."""

CHOSEN = {
    "span": Chosen("fewest", FEWEST.chooser),
    "origin": Chosen("newest", NEWEST.chooser),
    "end_time": Chosen("lower", END_TIME.generic[0].chooser),
}


def migrated(rows, configuration=REFERENCE, history=(), **choosers):
    return step.transform(
        rows, resolutions=under(configuration, **choosers), history=history
    )


# ----- the boundary: what the step reads, and what it refuses ----------------------------


def test_the_malformed_release_is_read_as_such():
    stop = parse(RECLAIMED).stops[0]
    assert isinstance(stop, _Malformed)
    assert (stop.seq, stop.reason, stop.honored, stop.claim_seq) == (
        4,
        "reclaimed: slurm TIMEOUT",
        ("stop@3",),
        1,
    )
    assert stop.created_at == 1001.0  # its only time: the body has no t


def test_a_claim_carries_its_launch_id():
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0), rid="L1"),
        r(2, "lifecycle.started", _started("local://n1/12", 2.0)),  # hand-run
    ]
    assert [c.launch_id for c in parse(rows).claims] == ["L1", None]


def test_a_well_formed_stopped_is_read_as_such():
    (stop,) = parse(EXIT).stops
    assert isinstance(stop, _Stopped)
    assert (
        stop.seq,
        stop.completed,
        stop.error,
        stop.final_step,
        stop.claim_seq,
        stop.honored,
        stop.t,
    ) == (4, False, None, 0, 1, ("halt",), 3.0)


@pytest.mark.parametrize(
    "body",
    [
        {"reason": "reclaimed: slurm TIMEOUT"},  # 0.2.0's, never named
        {**_stopped(False, None, 0, 1, [], 1.0), "value": 0.5},  # a torn write
        {k: v for k, v in _stopped(False, None, 0, 1, [], 1.0).items() if k != "t"},
        _stopped(True, "boom", 0, 1, [], 1.0),  # completed with an error
        _stopped(False, None, 0, True, [], 1.0),  # a bool is no seq
        _stopped(False, None, 0, 1, ["a", "a"], 1.0),
        {**_release("x", [], 1), "t": 1.0},
        _release(None, [], 1),
        {},
    ],
)
def test_a_stopped_of_any_other_shape_is_refused_by_seq(body):
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.stopped", body),
    ]
    with pytest.raises(MigrationError, match=r"lifecycle\.stopped at seq 2 "):
        parse(rows)
    with pytest.raises(MigrationError, match=r"lifecycle\.stopped at seq 2 "):
        transform(rows)


@pytest.mark.parametrize(
    "topic, body",
    [
        ("lifecycle.started", {"t": 1.0}),
        ("lifecycle.started", {"handle": "local://n1/11", "t": 1.0, "x": 1}),
        ("lifecycle.heartbeat", {"step": 0, "consumed_seq": 0, "t": 1.0}),
        ("lifecycle.heartbeat", _beat(-1, 0, 1, 1.0)),
        ("lifecycle.heartbeat", _beat(0.5, 0, 1, 1.0)),
        ("value", {"value": 1.0, "t": 1.0}),
        ("value", {**_value(1.0, 0, 1.0), "unit": "s"}),
        ("value", _value(1.0, "3", 1.0)),
    ],
)
def test_a_record_the_step_reads_must_be_0_3_0s(topic, body):
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, topic, body, name="loss" if topic == "value" else None),
    ]
    with pytest.raises(MigrationError, match=rf"{topic} at seq 2 "):
        parse(rows)


@pytest.mark.parametrize(
    "first",
    [
        ("lifecycle.stopped", {"reason": "x"}),
        ("value", {"value": 1.0}),
        ("lifecycle.heartbeat", _beat(0, 0, 9, 1.0)),  # names no claim
        ("value", _value(0.5, 0, 1.0)),  # a null name
        ("control.subscribe", {}, "s"),  # a subscription
        ("lifecycle.bound", {"claim_seq": 1}, "s"),  # a subscription's binding
        ("control.stop", {"from": {"step": 3}}, "h"),  # a step atom
        ("launcher.terminated", {"reason": "exited"}, "L1"),  # not launcher-v0.4's
    ],
)
def test_a_refusal_names_the_first_record_the_step_cannot_read(first):
    """In seq order, whatever the topics: a malformed claim after it does not
    pre-empt it, and the boundary refuses it too."""
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, *first),
        r(3, "lifecycle.started", {"t": 2.0}),
        r(4, "lifecycle.heartbeat", {"step": 0}),
    ]
    with pytest.raises(MigrationError, match=r" at seq 2 "):
        parse(rows)


@pytest.mark.parametrize("claim", [2, 3, 4])
def test_a_heartbeat_must_name_a_claim_before_it(claim):
    """It names a value (2), itself (3), or a later claim (4)."""
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.1, 0, 1.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, claim, 2.0)),
        r(4, "lifecycle.started", _started("local://n2/22", 3.0)),
    ]
    with pytest.raises(MigrationError, match=r"heartbeat at seq 3 names claim"):
        parse(rows)


@pytest.mark.parametrize(
    "body", [_stopped(False, None, 0, 4, [], 2.0), _release("x", [], 2)]
)
def test_a_stopped_must_name_a_claim_before_it(body):
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.1, 0, 1.0), name="loss"),
        r(3, "lifecycle.stopped", body),
        r(4, "lifecycle.started", _started("local://n2/22", 3.0)),
    ]
    with pytest.raises(MigrationError, match=r"stopped at seq 3 names claim"):
        parse(rows)


@pytest.mark.parametrize("consumed", [3, 4, 9])
def test_a_heartbeats_watermark_must_name_a_record_before_it(consumed):
    """A nonzero consumed_seq names itself (3), a later record (4) or none (9):
    no 0.3.0 writer wrote one, and renumbering cannot carry it."""
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "control.stop", {}, rid="halt"),
        r(3, "lifecycle.heartbeat", _beat(0, consumed, 1, 2.0)),
        r(4, "value", _value(0.1, 0, 3.0), name="loss"),
    ]
    match = r"heartbeat at seq 3 has consumed_seq \d, which names no record before it"
    with pytest.raises(MigrationError, match=match):
        parse(rows)
    with pytest.raises(MigrationError, match=match):
        transform(rows)


# Watermarks naming a heartbeat with a step, each refused. A watermark is a read
# position in the control order, and step 0's commit moves after its late eval,
# so renaming it to the moved commit changes what it covers.
#
# Step 1's watermark names step 0's beat, below the stop it had not drained;
# renamed, it would sit above the stop, and await_consumed would read the stop
# as accepted.
WATERMARK_COVERING_A_STOP_IT_NEVER_DRAINED = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "control.stop", {}, rid="halt"),
        r(4, "value", _value(0.5, 0, 4.0), name="eval"),
        r(5, "lifecycle.heartbeat", _beat(1, 2, 1, 5.0)),
    ]
)

# A stepless beat's watermark names step 0's beat; the beat stays and the
# commit moves, so the beat would precede the record its watermark names.
STEPLESS_WATERMARK_ON_A_MOVED_COMMIT = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(None, 2, 1, 3.0)),
        r(4, "value", _value(0.5, 0, 4.0), name="eval"),
    ]
)

# A displaced worker's watermark names another claim's commit: A's step 1 names
# B's step 0, which moves after its late eval, B's by position.
WATERMARK_ON_ANOTHER_CLAIMS_COMMIT = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.started", _started("local://n2/22", 3.0)),
        r(4, "lifecycle.heartbeat", _beat(0, 0, 3, 4.0)),
        r(5, "lifecycle.heartbeat", _beat(1, 4, 1, 5.0)),  # A's
        r(6, "value", _value(0.5, 0, 6.0), name="eval"),  # B's, by position
    ]
)


@pytest.mark.parametrize(
    "rows, beat, named",
    [
        (WATERMARK_COVERING_A_STOP_IT_NEVER_DRAINED, 5, 2),
        (STEPLESS_WATERMARK_ON_A_MOVED_COMMIT, 3, 2),
        (WATERMARK_ON_ANOTHER_CLAIMS_COMMIT, 5, 4),
    ],
    ids=["covering_a_stop", "stepless", "another_claims"],
)
def test_a_watermark_naming_a_heartbeat_with_a_step_is_refused(rows, beat, named):
    """Refused by seq, naming the record: the reference Worker sets a watermark
    only to a control record's seq. One naming a value or another record M6
    never moves is read as it is, as the goldens' are."""
    match = (
        rf"heartbeat at seq {beat} has consumed_seq {named}, which names the "
        rf"lifecycle\.heartbeat at seq {named}, step 0's commit"
    )
    with pytest.raises(MigrationError, match=match):
        parse(rows)
    with pytest.raises(MigrationError, match=match):
        transform(rows)


@pytest.mark.parametrize("steps", [[0, 0], [0, 2, 1]], ids=["repeat", "below"])
def test_an_in_claim_rewind_is_refused_by_seq(steps):
    """Two heartbeats of one step in one claim, or a step below an earlier
    one of the claim's: an in-claim rewind, which 0.3.0 did not record."""
    rows = [r(1, "lifecycle.started", _started("local://n1/11", 1.0))] + [
        r(2 + i, "lifecycle.heartbeat", _beat(s, 0, 1, 2.0 + i))
        for i, s in enumerate(steps)
    ]
    match = rf"heartbeat at seq {len(steps) + 1} .*in-claim rewind"
    with pytest.raises(MigrationError, match=match):
        parse(rows)
    with pytest.raises(MigrationError, match=match):
        transform(rows)


def test_stepless_beats_and_another_claims_steps_are_no_rewind():
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(None, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 3.0)),
        r(4, "lifecycle.heartbeat", _beat(None, 0, 1, 4.0)),
        r(5, "lifecycle.heartbeat", _beat(1, 0, 1, 5.0)),
        r(6, "lifecycle.started", _started("local://n1/12", 6.0)),
        r(7, "lifecycle.heartbeat", _beat(1, 0, 6, 7.0)),
    ]
    assert [b.step for b in parse(rows).beats] == [None, 0, None, 1, 1]


def test_records_the_step_does_not_interpret_pass_the_boundary_as_they_are():
    """A user's topic, whatever its body, and a stop the step only checks."""
    rows = [
        r(1, "user.note", {"any": ["shape"]}, name="n"),
        r(2, "control.stop", {"from": {"all": [{"time_seconds": 5}]}}, rid="h"),
    ]
    log = parse(_dated(rows))
    assert [
        (x.seq, x.topic, x.name, x.request_id, x.body, x.created_at)
        for x in log.records
    ] == [
        (1, "user.note", "n", None, {"any": ["shape"]}, 1000.25),
        (
            2,
            "control.stop",
            None,
            "h",
            {"from": {"all": [{"time_seconds": 5}]}},
            1000.5,
        ),
    ]


# ----- every carried record is 0.3.0's ---------------------------------------------------
#
# The step writes a record whose body 0.4.0 keeps with its stored text, so it
# reads every such record at the boundary as 0.3.0 pins it: an input it accepts
# never yields a row 0.4.0 rejects.


@functools.cache
def _stack_0_3_0():
    """Format 0.3.0's validators: the envelope, and each convention by the
    prefix of the topics it owns; ``value`` is a topic, not a prefix."""
    jsonschema = pytest.importorskip("jsonschema")
    root = Path(__file__).resolve().parent.parent / "protocol"

    def load(name):
        schema = json.loads((root / f"{name}.schema.json").read_text())
        return jsonschema.Draft202012Validator(schema)

    conventions = {
        "control.": load("subscription-v0.3"),
        "lifecycle.": load("lifecycle-v0.5"),
        "launcher.": load("launcher-v0.4"),
    }
    return load("envelope-v0.2"), conventions, load("value-v0.2")


def _invalid_under_0_3_0(rows):
    """The seqs of the rows format 0.3.0 rejects: the envelope, then the
    convention owning the topic. A user's topic answers to the envelope
    alone."""
    envelope, conventions, value = _stack_0_3_0()
    bad = []
    for x in rows:
        record = x._asdict() | {"body": json.loads(x.body)}
        del record["created_at"]
        owners = [v for k, v in conventions.items() if x.topic.startswith(k)]
        owners += [value] if x.topic == "value" else []
        if not all(v.is_valid(record) for v in [envelope, *owners]):
            bad.append(x.seq)
    return bad


# M10's malformed bodies, which the step reads as a third party's release.
INVALID_UNDER_0_3_0 = {"reclaimed": [4], "later_release": [5], "claimless_release": [2]}


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_golden_inputs_are_0_3_0_as_declared(rid):
    assert _invalid_under_0_3_0(GOLDEN[rid]) == INVALID_UNDER_0_3_0.get(rid, [])


H = "local://n1/11"
AT_1 = r(1, "lifecycle.started", _started(H, 1.0))

# Records 0.3.0 rejects, each refused at the boundary by its seq: the last row.
UNREADABLE = {
    # envelope-v0.2: no empty string, and a body that is an object
    "empty_topic": [AT_1, r(2, "", {})],
    "empty_name": [AT_1, r(2, "user.note", {}, name="")],
    "empty_request_id": [r(1, "lifecycle.started", _started(H, 1.0), rid="")],
    "empty_launch_id": [r(1, "launcher.launched", _launched(H, 0.5), rid="")],
    "body_not_an_object": [AT_1, r(2, "user.note", [1])],
    # a topic 0.3.0 does not define under a prefix it reserves
    "withdraw": [AT_1, r(2, "control.withdraw", {"t": 1.0}, rid="x")],
    "commit": [AT_1, r(2, "lifecycle.commit", {"claim_seq": 1})],
    "rewind": [AT_1, r(2, "lifecycle.rewind", {"claim_seq": 1})],
    "launcher_verb": [r(1, "launcher.reaped", {}, rid="L1")],
    # subscription-v0.3: a stop's request_id, and its StopTrigger
    "stop_without_request_id": [AT_1, r(2, "control.stop", {})],
    "stop_until": [AT_1, r(2, "control.stop", {"until": {"count": 1}}, rid="h")],
    "stop_unknown_atom": [AT_1, r(2, "control.stop", {"from": {"steps": 3}}, rid="h")],
    "stop_from_a_number": [AT_1, r(2, "control.stop", {"from": 3}, rid="h")],
    "stop_empty_any": [AT_1, r(2, "control.stop", {"from": {"any": []}}, rid="h")],
    "stop_two_atoms": [
        AT_1,
        r(2, "control.stop", {"from": {"step": 1, "time_seconds": 2}}, rid="h"),
    ],
    # lifecycle-v0.5's Nak
    "nak_reason": [
        AT_1,
        r(2, "lifecycle.nak", {"reason": "no", "message": ""}, rid="h"),
    ],
    "nak_without_message": [
        AT_1,
        r(2, "lifecycle.nak", {"reason": "malformed"}, rid="h"),
    ],
    # launcher-v0.4: a launch id on every launcher record, its bodies, and the
    # reason pairing, whether or not a claim names the launch
    "launched_without_launch_id": [r(1, "launcher.launched", _launched(H, 0.5))],
    "terminated_without_launch_id": [
        r(1, "launcher.terminated", _terminated("exited", 1, None, 1.0))
    ],
    "launched_handle": [
        r(
            1,
            "launcher.launched",
            {"handle": 11, "status": "running", "t": 0.5},
            rid="L1",
        )
    ],
    "launched_status": [
        r(1, "launcher.launched", {"handle": H, "status": "done", "t": 0.5}, rid="L1")
    ],
    "exited_without_code": [
        r(1, "launcher.terminated", _terminated("exited", None, None, 1.0), rid="L1")
    ],
    "killed_with_a_code": [
        r(1, "launcher.terminated", _terminated("killed", 0, 9, 1.0), rid="L1")
    ],
    "killed_by_a_bool": [
        r(1, "launcher.terminated", _terminated("killed", None, True, 1.0), rid="L1")
    ],
    "terminated_without_t": [
        r(1, "launcher.terminated", {"reason": "exited", "exit_code": 1}, rid="L1")
    ],
    "terminated_after_an_exit": [
        r(1, "launcher.launched", _launched(H, 0.5), rid="L1"),
        r(2, "lifecycle.started", _started(H, 1.0), rid="L1"),
        r(3, "lifecycle.stopped", _stopped(True, None, None, 2, [], 2.0)),
        r(4, "launcher.terminated", {"reason": "exited", "exit_code": 0}, rid="L1"),
    ],
}


@pytest.mark.parametrize("case", list(UNREADABLE))
def test_a_record_0_3_0_rejects_is_refused_by_its_seq(case):
    rows = UNREADABLE[case]
    last = rows[-1]
    assert _invalid_under_0_3_0(rows) == [last.seq]
    with pytest.raises(MigrationError, match=rf"\bat seq {last.seq}\b"):
        parse(rows)


def test_a_users_topic_under_supervisor_is_refused():
    """Valid 0.3.0, which left supervisor.* to users; 0.4.0 reserves it, so
    supervisor-v0.5 rejects the topic, and the step cannot carry it."""
    rows = [AT_1, r(2, "supervisor.note", {})]
    assert _invalid_under_0_3_0(rows) == []
    with pytest.raises(
        MigrationError, match=r"supervisor\.note at seq 2 .*0\.4\.0 reserves"
    ):
        parse(rows)


# A stopped honoring the empty id, of each class, the last row.
HONORS_THE_EMPTY_ID = {
    "later": [
        AT_1,
        r(2, "control.stop", {}, rid="halt"),
        r(3, "lifecycle.stopped", _stopped(True, None, None, 1, [], 3.0)),
        r(4, "lifecycle.stopped", _stopped(False, None, None, 1, ["", "halt"], 4.0)),
    ],
    "claimless": [
        AT_1,
        r(2, "lifecycle.stopped", _stopped(False, None, None, None, [""], 2.0)),
    ],
    "backstop": [
        AT_1,
        r(2, "lifecycle.stopped", _stopped(False, BACKSTOP, None, 1, [""], 2.0)),
    ],
    "exit": [
        AT_1,
        r(2, "lifecycle.stopped", _stopped(True, None, None, 1, [""], 2.0)),
    ],
}


@pytest.mark.parametrize("case", list(HONORS_THE_EMPTY_ID))
def test_a_stopped_honoring_the_empty_id_is_refused(case):
    """Valid 0.3.0, whose honored items are bare strings, but the empty id names
    no stop, since envelope-v0.2 forbids an empty request_id. 0.4.0 rejects it
    whoever wrote the stop: a third party's would become a withdrawal under it,
    and a worker's exit would keep it in honored, whose items lifecycle-v0.6
    holds non-empty. parse refuses it before it can tell an exit from a third
    party's."""
    rows = HONORS_THE_EMPTY_ID[case]
    last = rows[-1]
    assert _invalid_under_0_3_0(rows) == []
    with pytest.raises(
        MigrationError,
        match=rf"lifecycle\.stopped at seq {last.seq} honors the empty id",
    ):
        parse(rows)


def test_a_whole_number_float_is_refused_as_the_step_reads_integers_strictly():
    """0.3.0's schemas accept 3.0 as an integer; the step does not, and says so
    rather than calling the record not 0.3.0's."""
    rows = [AT_1, r(2, "lifecycle.heartbeat", _beat(3.0, 0, 1, 2.0))]
    assert _invalid_under_0_3_0(rows) == []
    with pytest.raises(
        MigrationError, match=r"heartbeat at seq 2 .*reads strictly.*3\.0"
    ):
        parse(rows)


# A launch's second terminated: a process ends once, and a run nothing claimed
# takes its launch's terminated as its verdict (§5), so two make it ambiguous.
SECOND_REAPS = {
    "an_exited_claim": [
        r(1, "launcher.launched", _launched(H, 0.5), rid="L1"),
        r(2, "lifecycle.started", _started(H, 1.0), rid="L1"),
        r(3, "lifecycle.stopped", _stopped(True, None, None, 2, [], 2.0)),
        r(4, "launcher.terminated", _terminated("exited", 0, None, 3.0), rid="L1"),
        r(5, "launcher.terminated", _terminated("exited", 0, None, 3.5), rid="L1"),
    ],
    "nothing_claimed": [
        r(1, "launcher.launched", _launched(H, 0.5), rid="L1"),
        r(2, "launcher.terminated", _terminated("exited", 0, None, 1.0), rid="L1"),
        r(3, "launcher.terminated", _terminated("killed", None, 9, 1.1), rid="L1"),
    ],
    "one_before_the_claim": [
        r(1, "launcher.launched", _launched(H, 0.5), rid="L1"),
        r(2, "launcher.terminated", _terminated("exited", 0, None, 0.7), rid="L1"),
        r(3, "lifecycle.started", _started(H, 1.0), rid="L1"),
        r(4, "launcher.terminated", _terminated("exited", 1, None, 3.5), rid="L1"),
    ],
}


@pytest.mark.parametrize("case", list(SECOND_REAPS))
def test_a_launchs_second_terminated_is_refused_whatever_the_claims(case):
    rows = SECOND_REAPS[case]
    last = rows[-1]
    assert _invalid_under_0_3_0(rows) == []
    with pytest.raises(
        MigrationError, match=rf"launcher\.terminated at seq {last.seq} is a second"
    ):
        parse(rows)


# ----- refusals through the runner (Review Focus 5) -----------------------------------
#
# Each run is refused before the seal, by a message naming the run and the seq:
# the old log stays unsealed and writable, and no 0.4.0 log is written. Every
# claim's handle is dead, so is_live lets the step decide.


@pytest.fixture
def registered(monkeypatch):
    """The step registered for the test only, as tests/test_migrate.py's toy
    fixture registers its steps: the format does not change in this round."""
    monkeypatch.setitem(FORMATS, "0.4.0", DirectoryLayout("0.4.0"))
    monkeypatch.setattr(migrations, "STEPS", (V0_3_0_to_V0_4_0(),))


def _seed_0_3_0(root, rid, rows):
    """A 0.3.0 sqlite log at its address holding ``rows``, sent through a
    channel, which numbers them from 1."""
    assert [x.seq for x in rows] == list(range(1, len(rows) + 1))
    path = FORMATS["0.3.0"].sqlite_path(root, rid)
    path.parent.mkdir(exist_ok=True)
    ch = SqliteChannel(path)
    for x in rows:
        ch.send(json.loads(x.body), topic=x.topic, name=x.name, request_id=x.request_id)
    ch.close()
    return path


CLAIM = r(1, "lifecycle.started", _started(DEAD, 1.0))
LAUNCH = r(1, "launcher.launched", _launched(DEAD, 0.5), rid="L1")
LAUNCHED_CLAIM = r(2, "lifecycle.started", _started(DEAD, 1.0), rid="L1")
REAP = _terminated("exited", 1, None, 4.0)

REFUSED = {
    # what 0.4.0 cannot hold: any subscription, with its samples, its binding and its unsubscribe
    "subscribe": [CLAIM, r(2, "control.subscribe", {}, rid="s", name="loss")],
    "sample": [CLAIM, r(2, "value", _value(0.5, 0, 1.0), rid="s", name="loss")],
    "bound": [CLAIM, r(2, "lifecycle.bound", {"claim_seq": 1}, rid="s")],
    "unsubscribe": [CLAIM, r(2, "control.unsubscribe", {}, rid="s")],
    # a stop whose from holds a step atom, at any depth
    "step_atom": [CLAIM, r(2, "control.stop", {"from": {"step": 3}}, rid="h")],
    "nested_step_atom": [
        CLAIM,
        r(
            2,
            "control.stop",
            {"from": {"any": [{"time_seconds": 5}, {"all": [{"step": 3}]}]}},
            rid="h",
        ),
    ],
    # two heartbeats of one step in one claim
    "rewind": [
        CLAIM,
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 3.0)),
    ],
    # a null name, which value-v0.3 cannot hold
    "nameless": [CLAIM, r(2, "value", _value(0.5, 0, 1.0))],
    # The boundary: an empty name, which envelope-v0.2 forbids
    "empty_name": [CLAIM, r(2, "value", _value(0.5, 0, 1.0), name="")],
    # The boundary: a user's topic under the prefix 0.4.0 reserves
    "supervisor_topic": [CLAIM, r(2, "supervisor.note", {})],
    # The boundary: an empty id in a stopped's honored, which names no stop
    "honors_the_empty_id": [
        CLAIM,
        r(2, "lifecycle.stopped", _stopped(False, None, None, None, [""], 2.0)),
    ],
    # The boundary: a launch's second terminated, whatever the claims
    "two_reaps": [
        LAUNCH,
        LAUNCHED_CLAIM,
        r(3, "launcher.terminated", REAP, rid="L1"),
        r(4, "launcher.terminated", REAP, rid="L1"),
    ],
    "two_reaps_unclaimed": [
        LAUNCH,
        r(2, "launcher.terminated", REAP, rid="L1"),
        r(3, "launcher.terminated", REAP, rid="L1"),
    ],
    # is_live, which the runner asks before parse can refuse: a claim whose body
    # is not an object holds no handle
    "started_not_an_object": [r(1, "lifecycle.started", [DEAD, 1.0])],
}


@pytest.mark.parametrize("case", list(REFUSED))
def test_a_refusal_comes_before_the_seal_and_names_the_run_and_the_seq(
    registered, tmp_path, case
):
    rows = REFUSED[case]
    old = _seed_0_3_0(tmp_path, "r1", rows)
    last = rows[-1]
    (refused,) = migrate(
        SqliteStore(tmp_path), ["r1"], to="0.4.0", choices=NONE
    ).refused
    assert re.match(
        rf"^run 'r1': the {re.escape(last.topic)} at seq {last.seq} ", refused.message
    )
    assert os.stat(old).st_mode & 0o200  # not sealed
    assert not FORMATS["0.4.0"].sqlite_path(tmp_path, "r1").exists()
    ch = SqliteChannel(old)  # still writable
    assert ch.send({}, topic="user.note") == len(rows) + 1
    ch.close()


# ----- liveness, read as 0.3.0 read it ------------------------------------------------

FOREIGN = "local://otherhost/1"  # unresolvable here: reads as live


def test_is_live_is_the_latest_claim_that_no_stopped_names():
    claim = r(2, "lifecycle.started", _started(FOREIGN, 1.0))
    assert not step.is_live([])
    assert step.is_live([claim])
    named = _stopped(False, None, None, 2, [], 2.0)
    assert not step.is_live([claim, r(3, "lifecycle.stopped", named)])


def test_a_third_partys_or_a_malformed_stopped_naming_the_claim_releases_it():
    claim = r(1, "lifecycle.started", _started(FOREIGN, 1.0))
    backstop = _stopped(False, BACKSTOP, 0, 1, [], 2.0)
    assert not step.is_live([claim, r(2, "lifecycle.stopped", backstop)])
    assert not step.is_live([claim, r(2, "lifecycle.stopped", _release("x", [], 1))])


def test_a_stopped_naming_another_claim_or_none_releases_nothing():
    first = r(1, "lifecycle.started", _started(FOREIGN, 1.0))
    latest = r(2, "lifecycle.started", _started(FOREIGN, 2.0))
    earlier = _stopped(False, None, 0, 1, [], 3.0)  # the displaced worker's exit
    claimless = _stopped(False, None, None, None, [], 3.0)
    unnamed = {"reason": "x"}  # names nothing, as 0.3.0's tolerant read took it
    for body in (earlier, claimless, unnamed):
        assert step.is_live([first, latest, r(3, "lifecycle.stopped", body)])


def test_a_bool_names_no_claim():
    """JSON ``true`` is not the integer 1."""
    claim = r(1, "lifecycle.started", _started(FOREIGN, 1.0))
    assert step.is_live([claim, r(2, "lifecycle.stopped", {"claim_seq": True})])


def test_a_stopped_before_the_claim_releases_nothing():
    """0.3.0 read only the window after the claim: nothing before it can speak
    for it."""
    rows = [
        r(1, "lifecycle.stopped", {"claim_seq": 2}),
        r(2, "lifecycle.started", _started(FOREIGN, 1.0)),
    ]
    assert step.is_live(rows)


def test_a_dead_handle_is_not_live():
    assert not step.is_live([r(1, "lifecycle.started", _started(DEAD, 0.0))])


def test_the_launcher_tier_does_not_release_a_claim():
    rows = [
        r(1, "lifecycle.started", _started(FOREIGN, 1.0), rid="L1"),
        r(
            2,
            "launcher.terminated",
            {"reason": "killed", "exit_code": None, "signal": 9, "t": 2.0},
            rid="L1",
        ),
    ]
    assert step.is_live(rows)


@pytest.mark.parametrize(
    "body",
    [{"t": 0.0}, {"handle": 11, "t": 0.0}, [FOREIGN, 0.0]],
    ids=["no_handle", "not_a_string", "not_an_object"],
)
def test_a_claim_without_a_handle_cannot_be_judged(body):
    """``is_live`` reads the claim before ``parse`` can refuse it, so it refuses
    a body that holds no handle itself, an array's included."""
    with pytest.raises(
        MigrationError, match=r"^the lifecycle\.started at seq 1 carries no handle"
    ):
        step.is_live([r(1, "lifecycle.started", body)])


# ----- the step's place in the chain ----------------------------------------------------


def test_the_step_renumbers_and_is_not_registered_yet():
    """The format changes only at the end of layer 2 (log-formats §2, rule 3)."""
    assert (step.FROM, step.TO, step.RENUMBERS) == ("0.3.0", "0.4.0", True)
    assert step.FROM == RENUMBERING_FROM
    assert not any(isinstance(s, V0_3_0_to_V0_4_0) for s in STEPS)
    assert "0.4.0" not in FORMATS


# ----- a retained step carries its formats' semantics ------------------------------------


def test_the_step_imports_no_record_semantics():
    """log-formats §6: the step is retained forever, so it must read 0.3.0 and
    write 0.4.0 as they were defined, whatever later code does. From the package
    it takes only Row, MigrationError, resolve(), an OS probe of a handle rather
    than a format rule, the shared mechanism of ambiguity.py, and its own engine
    and facts modules, which are part of it; never runstate.lineage."""
    import ast
    import inspect

    import runstate.migrations.v0_3_0_to_v0_4_0 as mod

    tree = ast.parse(inspect.getsource(mod))
    internal = {
        (n.level, n.module, a.name)
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
        and (n.level > 0 or (n.module or "").startswith("runstate"))
        for a in n.names
    }
    own = {"ambiguity", "v0_3_0_to_v0_4_0_engine", "v0_3_0_to_v0_4_0_facts"}
    assert {x for x in internal if x[1] not in own} == {
        (1, None, "MigrationError"),
        (1, None, "Row"),
        (1, None, "v0_3_0_to_v0_4_0_engine"),
        (1, None, "v0_3_0_to_v0_4_0_facts"),
        (2, "vocabulary.handle", "resolve"),
    }
    assert {level for level, module, _ in internal if module in own} == {1}
    plain = {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert not {m for m in plain if m.startswith("runstate")}


def _internal_imports(mod):
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(mod))
    internal = {
        (n.level, n.module, a.name)
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
        and (n.level > 0 or (n.module or "").startswith(("runstate", "tests")))
        for a in n.names
    }
    plain = {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    return internal, plain


def test_the_engine_imports_only_its_facts():
    """The candidate engine is part of the step, under its import rule: from the
    package it takes Row, MigrationError and its own facts module, and nothing of
    the simulator, which is the other encoding the oracle checks it against."""
    import runstate.migrations.v0_3_0_to_v0_4_0_engine as engine

    internal, plain = _internal_imports(engine)
    assert {(level, module) for level, module, _ in internal} == {
        (1, None),
        (1, "v0_3_0_to_v0_4_0_facts"),
    }
    assert {name for level, module, name in internal if module is None} == {
        "MigrationError",
        "Row",
    }
    assert not {m for m in plain if m.startswith(("runstate", "tests"))}


def test_the_facts_module_imports_only_row():
    import runstate.migrations.v0_3_0_to_v0_4_0_facts as facts

    internal, plain = _internal_imports(facts)
    assert internal == {(1, None, "Row")}
    assert not {m for m in plain if m.startswith(("runstate", "tests"))}


def test_the_simulator_never_imports_the_step():
    """The two encodings stay independent: only the harness modules (the oracle,
    the mutation test, the goldens harness) import both."""
    import tests.migration_sim.build
    import tests.migration_sim.enumerate
    import tests.migration_sim.history
    import tests.migration_sim.render
    import tests.migration_sim.truth

    for mod in (
        tests.migration_sim.build,
        tests.migration_sim.enumerate,
        tests.migration_sim.history,
        tests.migration_sim.render,
        tests.migration_sim.truth,
    ):
        internal, plain = _internal_imports(mod)
        named = {module or "" for _, module, _ in internal} | plain
        assert not {m for m in named if "v0_3_0_to_v0_4_0" in m}, mod.__name__


def _watermark_on(stop_body):
    """A beats, B claims over it, a third party's stop names A, and B's beat's
    watermark (the log's tail, as a ``tail`` writer fills it) names that stop."""
    return _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
            r(3, "lifecycle.started", _started("local://n1/12", 3.0)),
            r(4, "lifecycle.stopped", stop_body),
            r(5, "lifecycle.heartbeat", _beat(None, 4, 3, 5.0)),
        ]
    )


def test_a_watermark_naming_a_stop_that_leaves_the_log_is_refused():
    """A third party's note on an ended claim becomes only its withdrawals, so
    nothing is left for the watermark to name."""
    rows = _watermark_on(_stopped(False, None, 0, 1, [], 4.0))
    with pytest.raises(
        MigrationError,
        match=r"heartbeat at seq 5 has consumed_seq 4, which names the "
        r"lifecycle\.stopped at seq 4, which leaves the log",
    ):
        migrated(rows, NOTES, **CHOSEN)


# ----- the declarations: facts, projections, choosers ------------------------------------


def test_the_unstated_facts_are_every_facts_whole_domain():
    (w,) = UNSTATED.workers.values()
    (tp,) = UNSTATED.third_parties.values()
    for f in dataclasses.fields(w):
        assert getattr(w, f.name) == E.domain(V.WorkerFacts, f.name)
    for f in dataclasses.fields(tp):
        assert getattr(tp, f.name) == E.domain(V.ThirdPartyFacts, f.name)
    assert FACTS.unstated is UNSTATED and FACTS.kind is V.Configuration


def test_the_manifest_renders_each_versions_facts_and_the_classifiers_identity():
    d = FACTS.describe(REFERENCE)
    assert d["classifier"] == f"{__name__}:_Reference"
    assert d["workers"]["ref"]["stride"] == ["next"]
    assert d["workers"]["ref"]["rewinds"] == []
    assert sorted(d["third_parties"]) == ["tp"]
    json.dumps(d)  # it is JSON


def _question(*values):
    key = E.Key(1, E.Projection.SPAN, 0)
    return E.Question(key, tuple(E.Answer(v, lambda: ()) for v in values))


def _commit(nth, parent):
    return E.PCommit(1, nth, nth, parent, E.Gaps(1, 1, 1))


def test_fewest_and_most_count_iterations_then_holds_then_take_the_newest_node():
    none, one = (), (_commit(0, None),)
    two = (_commit(0, None), _commit(1, E.CommitId(1, 0)))
    held = (E.PHold(5, 1, 0),)
    q = _question(none, one, two)
    assert FEWEST.chooser.choose(q).value == none
    assert MOST.chooser.choose(q).value == two
    assert FEWEST.chooser.choose(_question(held, none)).value == none
    assert MOST.chooser.choose(_question(held, none)).value == held
    older = (_commit(0, E.CommitId(1, 0)),)
    newer = (_commit(0, E.CommitId(3, 0)),)
    assert FEWEST.chooser.choose(_question(older, newer)).value == newer


def test_a_builtin_refuses_answers_it_cannot_tell_apart():
    a = (E.PHold(5, 1, 0),)
    b = (E.PHold(6, 1, 0),)
    assert FEWEST.chooser.choose(_question(a, b)) == Refusal(
        "2 answers tie under fewest"
    )


def test_newest_continues_the_newest_node_and_no_node_is_oldest():
    q = _question(
        (E.Resumes(5, None),),
        (E.Resumes(5, E.CommitId(1, 3)),),
        (E.Resumes(5, E.ExitId(1)),),
    )
    assert NEWEST.chooser.choose(q).value == (E.Resumes(5, E.ExitId(1)),)


def test_the_end_time_choosers_and_the_answers_shape():
    lower, upper = (b.chooser for b in END_TIME.generic)
    ticks = (E.Tick(6, 6.0), E.Tick(8, 8.0))
    both = EndTime(1, 0, ticks, Undated(2.0, (3.0, 4.0)))
    assert lower.choose(both) == EndedAt(2.0) and upper.choose(both) == DatedBy(8)
    undated = EndTime(1, 0, (), Undated(2.0, (3.0, 4.0)))
    assert lower.choose(undated) == EndedAt(2.0)
    assert upper.choose(undated) == EndedAt(3.0)
    last = EndTime(1, 0, (), Undated(2.0, ()))  # after the log's last record
    assert upper.choose(last) == EndedAt(2.0)
    dated = EndTime(1, 0, ticks, None)
    assert lower.choose(dated) == DatedBy(6) and upper.choose(dated) == DatedBy(8)
    assert END_TIME.check(both, EndedAt(5)) == EndedAt(5.0)
    assert END_TIME.check(both, DatedBy(6)) == DatedBy(6)
    for q, bad in (
        (both, EndedAt(1.0)),
        (both, EndedAt("3")),
        (both, DatedBy(7)),
        (dated, EndedAt(9.0)),
        (both, 5.0),
    ):
        with pytest.raises(ValueError):
            END_TIME.check(q, bad)


SPAN_CHECK = SPAN.check


def test_a_fork_answer_must_be_one_of_its_own():
    q = _question((), (E.PHold(5, 1, 0),))
    assert SPAN_CHECK(q, q.answers[1]) is q.answers[1]
    with pytest.raises(ValueError, match="none of the fork's own"):
        SPAN_CHECK(q, E.Answer((E.PHold(6, 1, 0),), lambda: ()))


def test_a_logs_birth_is_read_from_the_manifest():
    from runstate.migrations.v0_3_0_to_v0_4_0 import _born

    earlier = Entry(
        run_id="r",
        from_format="0.2.0",
        to_format="0.3.0",
        runstate="x",
        step_source="sha256:0",
        at=0.0,
        limits=LIMITS,
        assumed=None,
        facts=None,
        resolved={"record-claim": "positional"},
        readings={},
        accepted={},
    )
    assert _born(()) == V.Born030()
    assert _born((earlier,)) == V.Born020()


def test_a_malformed_stopped_is_read_only_by_a_consumers_classifier():
    with pytest.raises(
        MigrationError,
        match=r"lifecycle\.stopped at seq 4 has a body format 0\.3\.0 does not "
        r"define .* only a consumer's classifier may read it",
    ):
        transform(RECLAIMED)


def test_the_step_reports_each_fork_with_the_facts_that_could_settle_it():
    rows = _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "value", _value(0.9, 0, 2.0), name="loss"),
            r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 3.0)),
            r(4, "lifecycle.started", _started("local://n1/12", 4.0)),
        ]
    )
    wide = REFERENCE._replace(
        workers={
            "ref": dataclasses.replace(
                REFERENCE.workers["ref"], load=frozenset(V.Load) - {V.Load.OUTSIDE}
            )
        }
    )
    a = Assumptions(wide, "test", LIMITS)
    found = step.detect(rows, assumptions=a, history=())
    assert set(found.forks) == {p.name for p in step.PROJECTIONS}
    origin = found.forks["origin"]
    assert origin and all(f.seq == 4 for f in origin)
    assert all("load" in f.settled_by for f in origin)
    out = migrated(rows, wide, **CHOSEN)
    assert set(out.readings) == {p.name for p in step.PROJECTIONS}
    assert out.readings["origin"][4] == {
        "4": [
            {
                "is": "Resumes",
                "claim": 4,
                # the newest node: claim 1's run-on, asked before claim 1's span
                "resume": {"is": "CommitId", "claim": 1, "nth": 1},
            }
        ]
    }


def test_a_run_past_the_budget_is_refused_naming_the_record():
    tight = Resolutions(
        Assumptions(REFERENCE, "test", Limits(cap=3000, budget=3, run_on=2)),
        under(REFERENCE, **CHOSEN).choosers,
    )
    with pytest.raises(MigrationError, match=r"more than 3 states summed .* seq \d"):
        step.transform(GOLDEN["exit"], resolutions=tight, history=())


def _a_claim_then_another():
    return _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "value", _value(0.9, 0, 2.0), name="loss"),
            r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 3.0)),
            r(4, "lifecycle.started", _started("local://n1/12", 4.0)),
        ]
    )


@pytest.mark.parametrize("run_on", [0, 2, 4])
def test_a_run_on_cut_at_the_horizon_refuses_naming_the_claim_and_the_facts(run_on):
    """--run-on is a limit, never a reading: with ``beat-cov`` and ``marker``
    unstated, each claim may run any number of silent passes no record shows, so a
    candidate reaches the horizon however far it lies, and the run is refused."""
    unbounded = REFERENCE._replace(
        workers={
            "ref": dataclasses.replace(
                REFERENCE.workers["ref"],
                load=frozenset(V.Load) - {V.Load.OUTSIDE},
                marker=frozenset(V.Marker),
                beat_cov=frozenset(V.Covered),
            )
        }
    )
    res = under(unbounded, **CHOSEN)
    res = res._replace(
        assumptions=res.assumptions._replace(
            limits=Limits(cap=3000, budget=6_000_000, run_on=run_on)
        )
    )
    with pytest.raises(
        MigrationError,
        match=r"a candidate of claims 1, 4 runs on, or loads from outside, to the horizon "
        rf"\({run_on} labels .* \(marker, beat-cov, stride\), or raise --run-on",
    ):
        step.transform(_a_claim_then_another(), resolutions=res, history=())


def test_no_stated_facts_names_the_facts_to_state_and_the_preset():
    """Under no stated facts a small log already exceeds the cap: the refusal says so,
    names the facts that widen the pass, and points at the reference preset."""
    with pytest.raises(MigrationError) as refused:
        step.detect(
            GOLDEN["exit"], assumptions=resolutions(step, NONE).assumptions, history=()
        )
    message = str(refused.value)
    assert "under no stated facts (no --assume)" in message
    assert "load, " in message and "stride" in message and "marker" in message
    assert '§7, "The writer facts"' in message
    assert "--assume runstate.migrations.v0_3_0_to_v0_4_0:REFERENCE" in message


def test_the_reference_preset_is_named_on_the_command_line_and_migrates_its_log():
    """A consumer names the preset, giving its word that its worker is the reference
    Worker: the reference Worker's exit honouring its stop migrates under it."""
    choices = parse_choices(
        [step],
        assume=["runstate.migrations.v0_3_0_to_v0_4_0:REFERENCE"],
        resolve=["span=fewest", "origin=newest", "end-time=lower"],
        accept=[],
        limits=LIMITS,
    )
    res = resolutions(step, choices)
    assert res.assumptions.facts is STEP.REFERENCE
    out = step.transform(EXIT, resolutions=res, history=())
    assert [x.topic for x in out.rows] == [
        "lifecycle.started",
        "lifecycle.commit",
        "control.stop",
        "lifecycle.stopped",
    ]
    assert json.loads(out.rows[3].body)["honored"] == ["halt"]
    assert FACTS.describe(STEP.REFERENCE)["classifier"] == (
        "runstate.migrations.v0_3_0_to_v0_4_0:Reference"
    )


def _the_reference_workers_log(kind):
    """A log the reference Worker writes: four passes ``done`` (completed), ``plain``
    (preempted), ``stop`` (honouring a stop at pass 1) or ``error`` (raising in pass
    2); ``resume``, four passes then a second claim resuming at step 4."""
    from runstate.channel import MemoryChannel
    from runstate.worker import Worker

    clock = itertools.count(1.0)
    ch = MemoryChannel()

    def passes(start, total):
        with Worker(ch, now=lambda: next(clock)) as w:
            w.set("lr", 0.1)
            for s in w.steps(total, start=start):
                w.emit("loss", 1.0 / (s + 1))
                if kind == "stop" and s == 1:
                    ch.send({}, topic="control.stop", request_id="halt")
                if kind == "error" and s == 2:
                    raise RuntimeError("boom")
            if kind == "done":
                w.stopped(completed=True)

    with contextlib.suppress(RuntimeError):
        passes(0, 4)
    if kind == "resume":
        passes(4, 6)
    return [
        Row(e.seq, e.topic, e.name, e.request_id, json.dumps(e.body), float(e.seq))
        for e in ch.read(after=0)
    ]


@pytest.mark.parametrize("kind", ["done", "plain", "stop", "error", "resume"])
def test_the_reference_workers_own_logs_migrate_under_the_preset(kind):
    """The preset's word is the reference Worker's code: every log it writes is
    written by some history the preset states, and nothing diverges but the value of
    the pass an error cut, which no lineage holds."""
    choices = parse_choices(
        [step],
        assume=["runstate.migrations.v0_3_0_to_v0_4_0:REFERENCE"],
        resolve=["span=fewest", "origin=newest", "end-time=lower"],
        accept=[],
        limits=LIMITS,
    )
    res = resolutions(step, choices)
    rows = _the_reference_workers_log(kind)
    step.detect(rows, assumptions=res.assumptions, history=())
    out = step.transform(rows, resolutions=res, history=())
    fired = {d: seqs for d, seqs in out.divergences.items() if seqs}
    assert fired == ({"unheld-value": (6,)} if kind == "error" else {})
    assert [x.topic for x in out.rows].count("lifecycle.started") == (
        2 if kind == "resume" else 1
    )
