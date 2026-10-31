"""The 0.3.0 -> 0.4.0 step (commits-and-lineage.md §7; log-formats.md §6): what it
reads and refuses at the boundary, its liveness, its place in the chain, its
declarations (facts, projections, choosers, divergences), and its import rule.

The golden logs live in ``tests/migration_goldens.py``; their whole outputs, each
golden restated as (log, facts, choices) -> output, are pinned in
``tests/test_migration_goldens.py``, which states each golden's facts through its
simulator history."""

import dataclasses
import functools
import json
import os
import re
from pathlib import Path

import pytest

import runstate.migrations.v0_3_0_to_v0_4_0_engine as E
from runstate import migrations
from runstate.channel.sqlite import SqliteChannel
from runstate.formats import FORMATS
from tests.format_helpers import toy_layout
from runstate.migrations import RENUMBERING_FROM, STEPS, MigrationError, migrate
from runstate.migrations.ambiguity import (
    Assumed,
    Assumptions,
    Choices,
    Chosen,
    Entry,
    History,
    Limits,
    Trace,
    Refusal,
    Resolutions,
    Undecided,
    parse_choices,
    resolutions,
)
from runstate.migrations.seal import seal_sqlite
from runstate.migrations.stores import SqliteStore
from runstate.migrations.v0_3_0_to_v0_4_0 import (
    END_TIME,
    FACTS,
    FEWEST,
    MOST,
    NEWEST,
    SPAN,
    UNSTATED,
    WRITER_NEWEST,
    DatedBy,
    EndedAt,
    EndTime,
    Unlogged,
    V0_3_0_to_V0_4_0,
    _Malformed,
    _Stopped,
    parse,
)
import runstate.migrations.v0_3_0_to_v0_4_0 as STEP
import runstate.migrations.v0_3_0_to_v0_4_0_facts as V
from tests.migration_helpers import (
    CHOSEN,
    CONSUMER,
    LIMITS,
    NO_HISTORY,
    a_beacon_between_two_places,
    a_claim_then_another,
    a_watermark_on,
    preset,
    reference_worker_log,
    under,
)
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
NONE = parse_choices([step], assume=[], resolve=[], accept=[], limits=LIMITS)


def transform(rows):
    """The step under no stated facts: the boundary refuses before the engine."""
    return step.transform(
        rows, resolutions=resolutions(step, NONE), history=NO_HISTORY
    ).rows


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


CHECKPOINTED = STEP.reference(**CONSUMER).workers["reference"]
"""The reference preset under ``CONSUMER``."""

REFERENCE = V.Configuration(
    {
        "ref": dataclasses.replace(
            CHECKPOINTED,
            loading=frozenset({V.Loading.LOADED}),
            final_step=frozenset(V.FinalStep),
            error_final_step=frozenset({V.FinalStep.TICKED}),
            watermark=frozenset(V.Watermark),
        )
    },
    {"tp": UNSTATED.third_parties["any"]},
    _Reference(),
    frozenset({V.Birth.V0_3_0}),
)
"""``CHECKPOINTED`` widened for these tests' hand-written logs: a clock before the first
pass that reads the loaded step, any ``final_step`` at an exit, the last tick at an
error, any watermark, and any third party; narrow enough that a small log follows at
the cap."""


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


def migrated(rows, configuration=REFERENCE, history=NO_HISTORY, **choosers):
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
    root = Path(__file__).resolve().parent / "fixtures" / "log-format-0.3.0"

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
        MigrationError,
        match=r"heartbeat at seq 2 .*its step is a number .*3\.0, a whole-number float",
    ):
        parse(rows)


def test_an_unreadable_body_s_refusal_says_what_failed():
    """The refusal names the keys a body lacks, those its schema does not define, and
    each value of the wrong JSON type; it speaks of whole-number floats only where
    one failed (the final review's M3: the store's pre-0.2.0 claims hold
    ``attached_at`` and ``hostname``)."""
    attached = {**_started(H, 1.0), "attached_at": 1.0, "hostname": "n1"}
    cases = {
        "unknown": (
            [r(1, "lifecycle.started", attached)],
            "it holds attached_at, hostname, which the schema does not define",
        ),
        "missing": (
            [r(1, "lifecycle.started", {"handle": H})],
            "it lacks t",
        ),
        "ill_typed": (
            [AT_1, r(2, "lifecycle.heartbeat", _beat("3", 0, 1, 2.0))],
            "its step is a string the schema does not admit there",
        ),
        "a_reap_s_reason": (
            [
                r(1, "launcher.launched", _launched(H, 0.5), rid="L1"),
                r(
                    2,
                    "launcher.terminated",
                    _terminated("exited", None, 9, 1.0),
                    rid="L1",
                ),
            ],
            "its reason is exited, which needs an exit code and no signal",
        ),
    }
    for case, (rows, why) in cases.items():
        with pytest.raises(MigrationError) as refused:
            parse(rows)
        message = str(refused.value)
        assert why in message and "does not read as" in message, case
        assert "float" not in message, case


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
    monkeypatch.setitem(FORMATS, "0.4.0", toy_layout("0.4.0"))
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


def test_a_watermark_naming_a_stop_that_leaves_the_log_is_refused():
    """A third party's note on an ended claim becomes only its withdrawals, so
    nothing is left for the watermark to name."""
    rows = a_watermark_on(_stopped(False, None, 0, 1, [], 4.0))
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
    """``lower`` and ``upper`` order every answer by time, then by place: a tick at its
    own ``t`` in its own place, a place at its lower bound (``lower``) or at the next
    record's ``t`` (``upper``)."""
    lower, upper = (b.chooser for b in END_TIME.generic)
    ticks = (E.Tick(6, 6.0), E.Tick(8, 8.0))
    places = (Unlogged(3, 2.0, 3.5), Unlogged(4, 2.0, 4.0))
    both = EndTime(E.CommitId(1, 0), ticks, places)
    assert lower.choose(both) == EndedAt(3, 2.0)  # a tie on time: the earlier place
    assert upper.choose(both) == DatedBy(8)
    undated = EndTime(E.CommitId(1, 0), (), places)
    assert upper.choose(undated) == EndedAt(4, 4.0)
    last = EndTime(
        E.CommitId(1, 0), (), (Unlogged(9, 2.0, None),)
    )  # after the log's last record
    assert upper.choose(last) == EndedAt(9, 2.0)
    dated = EndTime(E.CommitId(1, 0), ticks, ())
    assert lower.choose(dated) == DatedBy(6) and upper.choose(dated) == DatedBy(8)
    tied = EndTime(E.CommitId(1, 0), (E.Tick(5, 4.0),), (Unlogged(4, 3.0, 4.0),))
    assert upper.choose(tied) == DatedBy(5)  # a tie on time: the later place
    # the next record is another writer's, whose clock runs behind the place's bound:
    # upper never answers below the bound, which the check would refuse
    skewed = EndTime(E.CommitId(1, 0), (), (Unlogged(4, 3.0, 2.5),))
    assert upper.choose(skewed) == EndedAt(4, 3.0)
    assert END_TIME.check(skewed, upper.choose(skewed)) == EndedAt(4, 3.0)
    assert END_TIME.check(both, EndedAt(4, 5)) == EndedAt(4, 5.0)
    assert END_TIME.check(both, DatedBy(6)) == DatedBy(6)
    for q, bad in (
        (both, EndedAt(3, 1.0)),
        (both, EndedAt(3, "3")),
        (both, EndedAt(5, 3.0)),
        (both, DatedBy(7)),
        (dated, EndedAt(3, 9.0)),
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


def test_a_logs_birth_is_read_from_evidence_then_the_statement():
    """Evidence first (an entry of the earlier step, a trace of the log in 0.2.0), then
    the statement; neither, and the run is refused."""
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
    sealed = Trace("0.2.0", "a row of runstate_v0_2_0.sealed_runs")
    for history in (History((earlier,), ()), History((), (sealed,))):
        assert _born(history, None) == V.Born020()
    assert _born(NO_HISTORY, frozenset({V.Birth.V0_3_0})) == V.Born030()
    assert _born(NO_HISTORY, frozenset({V.Birth.V0_2_0})) == V.Born020()
    assert _born(NO_HISTORY, frozenset(V.Birth)) == V.Born020()
    with pytest.raises(MigrationError, match="nothing shows where its log was born"):
        _born(NO_HISTORY, None)


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
    found = step.detect(rows, assumptions=a, history=NO_HISTORY)
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
        step.transform(GOLDEN["exit"], resolutions=tight, history=NO_HISTORY)


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
        match=r"a candidate of claims? [0-9, ]+ closes a pass at the horizon, its loop "
        rf"free to continue \({run_on} labels .*: marker, closing, beat-cov$",
    ):
        step.transform(a_claim_then_another(), resolutions=res, history=NO_HISTORY)


class _Closes(_Reference):
    """``_Reference``, its stepped values each its pass's last act."""

    def value(self, seq):
        return frozenset({V.ValueRole(V.Role.CLOSING, False)})


@pytest.mark.parametrize(
    "narrowed",
    [
        {"marker": frozenset({V.Marker.RECORDED})},
        {"closing": frozenset({V.Closing.AT_VALUE})},
        {"beat_cov": frozenset({V.Covered.BEATEN})},
    ],
    ids=["marker", "closing", "beat-cov"],
)
def test_each_fact_the_horizon_refusal_names_clears_it(narrowed):
    """Each fact the refusal names, stated narrowly, rules out a silent run long enough
    to reach the horizon, and the same log migrates."""
    w = REFERENCE.workers["ref"]
    unbounded = dataclasses.replace(
        w,
        load=frozenset(V.Load) - {V.Load.OUTSIDE},
        marker=frozenset(V.Marker),
        beat_cov=frozenset(V.Covered),
        closing=frozenset(V.Closing),
    )
    cleared = REFERENCE._replace(
        workers={"ref": dataclasses.replace(unbounded, **narrowed)}
    )
    if "closing" in narrowed:  # a pass then closes only at a value that closes it
        cleared = cleared._replace(classifier=_Closes())
    m = step.transform(
        a_claim_then_another(),
        resolutions=under(cleared, **CHOSEN),
        history=NO_HISTORY,
    )
    assert m.rows[0].topic == "lifecycle.started"


def test_no_stated_facts_names_the_facts_to_state_and_the_preset():
    """Under no stated facts a small log already exceeds the cap: the refusal says so,
    names the facts that widen the pass, and points at the reference preset. (Its
    birth is the manifest's: under no stated facts nothing else could date it.)"""
    with pytest.raises(MigrationError) as refused:
        step.detect(
            GOLDEN["exit"],
            assumptions=resolutions(step, NONE).assumptions,
            history=History((BORN_020,), ()),
        )
    message = str(refused.value)
    assert "under no stated facts (no --assume)" in message
    assert "load, " in message and "stride" in message and "marker" in message
    assert '§7, "The writer facts"' in message
    assert "runstate.migrations.v0_3_0_to_v0_4_0.reference(...)" in message


UNKNOWN = dict(
    stride=V.every(V.Stride),
    loading=V.every(V.Loading),
    closing=V.every(V.Closing),
    marker=V.every(V.Marker),
    values=V.every(V.Phase),
    loop_end=V.every(V.LoopEnd),
    discards=V.every(V.Discards),
    cut_save=V.every(V.CutSave),
    save_at=V.every(V.SaveAt),
    save_cov=V.every(V.Saved),
    leave_save=V.every(V.Saved),
    load=V.every(V.Load),
    resume=V.every(V.Resume),
    strays=V.every(V.Stray),
    born=frozenset(V.Birth),
)
"""A consumer that knows nothing of its own code passes each fact's whole domain,
explicitly, and states either birth, the wider reading, for a log no evidence dates."""

PRESET = STEP.reference(**UNKNOWN)
"""A consumer's own call of the preset, as its ``--assume`` names it."""

PRESET_CHECKPOINTED = STEP.reference(**{**CONSUMER, "closing": V.every(V.Closing)})
"""The preset under a consumer's word on its own code (``CONSUMER``), its bodies ending
a pass at a value or after another writer's record."""


def test_a_consumer_names_its_own_call_of_the_preset_and_migrates_its_log():
    """A consumer's ``--assume`` names its own call of ``reference``, giving its word
    that its worker is the reference Worker: the reference Worker's exit honouring its
    stop migrates under it, and the manifest records the classifier."""
    choices = parse_choices(
        [step],
        assume=["tests.test_migration_v0_3_0_to_v0_4_0:PRESET"],
        resolve=["span=fewest", "origin=newest", "end-time=lower"],
        accept=[],
        limits=LIMITS,
    )
    res = resolutions(step, choices)
    assert res.assumptions.facts.workers == PRESET.workers
    out = step.transform(EXIT, resolutions=res, history=NO_HISTORY)
    topics = [x.topic for x in out.rows]
    assert topics[0] == "lifecycle.started" and topics[-1] == "lifecycle.stopped"
    assert topics.count("lifecycle.commit") == 1 and "control.stop" in topics
    assert json.loads(out.rows[-1].body)["honored"] == ["halt"]
    assert FACTS.describe(PRESET)["classifier"] == (
        "runstate.migrations.v0_3_0_to_v0_4_0:Reference"
    )
    assert FACTS.describe(PRESET)["born"] == ["0.2.0", "0.3.0"]
    assert FACTS.describe(UNSTATED)["born"] is None


@pytest.mark.parametrize("missing", list(UNKNOWN))
def test_the_preset_requires_every_fact_the_consumers_code_decides(missing):
    """Each fact the consumer's own code decides is a required keyword-only argument:
    none is left wide unasked."""
    with pytest.raises(TypeError):
        STEP.reference(**{k: v for k, v in UNKNOWN.items() if k != missing})


def test_the_preset_states_what_the_worker_fixes():
    """The preset takes no positional argument, states what ``worker.py`` fixes, and
    takes the rest, every phase of ``values`` included, from the consumer: its
    classifier answers a value every worker role, so that ``values`` and ``loading`` do
    the narrowing."""
    with pytest.raises(TypeError):
        STEP.reference(*UNKNOWN.values())  # type: ignore[misc]
    w = PRESET.workers["reference"]
    assert (w.beat_pos, w.beat_cov) == ({V.BeatPos.AFTER_CLOSE}, {V.Covered.BEATEN})
    assert w.rewinds == frozenset()
    assert w.final_step == w.error_final_step == {V.FinalStep.YIELDED}
    assert (w.takeover, w.fold, w.honored, w.watermark) == (
        {V.Takeover.DEAD},
        {V.Fold.AT_CLAIM},
        {V.Honored.EXIT},
        {V.Watermark.CONTROL},
    )
    for k, v in UNKNOWN.items():
        assert (PRESET.born if k == "born" else getattr(w, k)) == v, k
    reader = PRESET.classifier.read(
        [
            r(1, "value", _value(0.5, 0, 1.0), name="a"),
            r(2, "value", _value(0.5, None, 1.0), name="b"),
        ]
    )
    worker_roles = set(V.Role) - {V.Role.STRAY}
    assert reader.value(1) == {V.ValueRole(x, False) for x in worker_roles}
    assert reader.value(2) == {V.ValueRole(x, True) for x in worker_roles}


@pytest.mark.parametrize("kind", ["done", "plain", "stop", "error", "resume"])
def test_the_reference_workers_own_logs_migrate_under_the_preset(kind):
    """The preset's word is the reference Worker's code: every log it writes is
    written by some history the preset states, and nothing diverges but the value of
    the pass an error cut, which no lineage holds."""
    choices = parse_choices(
        [step],
        assume=["tests.test_migration_v0_3_0_to_v0_4_0:PRESET_CHECKPOINTED"],
        resolve=["span=fewest", "origin=newest", "end-time=lower"],
        accept=[],
        limits=LIMITS,
    )
    res = resolutions(step, choices)
    rows = reference_worker_log(f"reference-{kind}")
    step.detect(rows, assumptions=res.assumptions, history=NO_HISTORY)
    out = step.transform(rows, resolutions=res, history=NO_HISTORY)
    fired = {d: seqs for d, seqs in out.divergences.items() if seqs}
    assert fired == ({"unheld-value": (6,)} if kind == "error" else {})
    assert [x.topic for x in out.rows].count("lifecycle.started") == (
        2 if kind == "resume" else 1
    )


# ----- where an undated commit stands is part of its end-time reading (fix round 2) ----


FIX1 = V.Configuration(
    {"reference": CHECKPOINTED}, {}, STEP.Reference(), frozenset({V.Birth.V0_3_0})
)
"""The reference preset as fix round 1 shipped it: the reference Worker with its
consumer's checkpoints (``CHECKPOINTED``)."""


class _Asked:
    """An end-time chooser that keeps each question it is asked and answers as a
    built-in does."""

    def __init__(self, builtin):
        self.builtin, self.asked = builtin, []

    def choose(self, question):
        self.asked.append(question)
        return self.builtin.chooser.choose(question)


def test_each_place_an_undated_commit_may_stand_is_its_own_answer():
    """Where the undated commit stands sets its lower bound, so each place is an
    answer, and the paths an answer keeps all write it there: ``lower`` writes it after
    the tick at its bound, ``upper`` after the beacon at the next record's ``t``."""
    rows = a_beacon_between_two_places()
    found = step.detect(
        rows, assumptions=under(FIX1, **CHOSEN).assumptions, history=NO_HISTORY
    ).forks
    assert {p: [f.seq for f in fs] for p, fs in found.items() if fs} == {
        "end-time": [1]
    }
    out = {}
    for builtin in END_TIME.generic:
        asked = _Asked(builtin)
        m = step.transform(
            rows,
            resolutions=under(FIX1, **{**CHOSEN, "end_time": Chosen("x", asked)}),
            history=NO_HISTORY,
        )
        assert asked.asked == [
            EndTime(
                E.CommitId(1, 1), (), (Unlogged(3, 3.0, 4.0), Unlogged(4, 4.0, 5.0))
            )
        ]
        out[builtin.name] = [(x.topic, json.loads(x.body)["t"]) for x in m.rows[2:]]
        assert m.readings["end-time"] == {
            1: {
                "1": (
                    {"is": "EndedAt", "after": 3, "t": 3.0}
                    if builtin.name == "lower"
                    else {"is": "EndedAt", "after": 4, "t": 5.0}
                )
            }
        }
    assert out["lower"] == [
        ("lifecycle.commit", 3.0),
        ("lifecycle.commit", 3.0),
        ("lifecycle.heartbeat", 4.0),
        ("lifecycle.stopped", 5.0),
    ]
    assert out["upper"] == [
        ("lifecycle.commit", 3.0),
        ("lifecycle.heartbeat", 4.0),
        ("lifecycle.commit", 5.0),
        ("lifecycle.stopped", 5.0),
    ]


def test_a_consumers_end_time_at_a_place_is_written_there():
    """A consumer's ``EndedAt`` after the beacon at a time between the two bounds is
    written after the beacon, never before it: the answer, not an order, places it."""

    class Late:
        def choose(self, question):
            return EndedAt(4, 4.5)

    m = step.transform(
        a_beacon_between_two_places(),
        resolutions=under(FIX1, **{**CHOSEN, "end_time": Chosen("x", Late())}),
        history=NO_HISTORY,
    )
    assert [(x.topic, json.loads(x.body)["t"]) for x in m.rows[3:5]] == [
        ("lifecycle.heartbeat", 4.0),
        ("lifecycle.commit", 4.5),
    ]
    with pytest.raises(MigrationError, match="precedes the lower bound 4.0 there"):

        class Early:
            def choose(self, question):
                return EndedAt(4, 3.5)

        step.transform(
            a_beacon_between_two_places(),
            resolutions=under(FIX1, **{**CHOSEN, "end_time": Chosen("x", Early())}),
            history=NO_HISTORY,
        )


class _Both:
    """A classifier that answers every value both a register and a metric, in any
    phase: which one a teardown value was, no reading reads."""

    def read(self, log):
        return self

    def started(self, seq):
        return frozenset({"ref"})

    def stopped(self, seq):
        return frozenset({V.OwnStop()})

    def value(self, seq):
        return frozenset(
            V.ValueRole(role, register)
            for role in (V.Role.PASS, V.Role.TEARDOWN)
            for register in (False, True)
        )


def test_a_value_the_classifier_answers_both_fires_both_divergences():
    """A stepped value after its claim's exit is unheld whichever the classifier's
    answer was, and the paths left keep both: register-value and unheld-value both
    fire there, since neither reading is the step's to pick."""
    both = REFERENCE._replace(
        workers={
            "ref": dataclasses.replace(
                REFERENCE.workers["ref"],
                values=frozenset({V.Phase.PASS, V.Phase.TEARDOWN}),
            )
        },
        third_parties={},
        classifier=_Both(),
    )
    rows = _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "value", _value(0.5, 0, 2.0), name="loss"),
            r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 3.0)),
            r(4, "lifecycle.stopped", _stopped(True, None, 0, 1, [], 4.0)),
            r(5, "value", _value(0.25, 0, 5.0), name="status"),
        ]
    )
    m = migrated(rows, both, **CHOSEN)
    fired = {d: seqs for d, seqs in m.divergences.items() if seqs}
    assert 5 in fired["register-value"] and 5 in fired["unheld-value"]


def test_the_step_drops_the_last_runs_candidates_before_building_the_next():
    """The step keeps one run's candidates, for its detect and transform; the next
    run's build starts with none held, so the budget bounds memory."""
    held = []
    real = E.candidates

    def watched(*args, **kwargs):
        held.append(mine._last)
        return real(*args, **kwargs)

    mine = STEP.V0_3_0_to_V0_4_0()
    res = under(FIX1, **CHOSEN)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(E, "candidates", watched)
        for rows in (EXIT, a_beacon_between_two_places()):
            mine.detect(rows, assumptions=res.assumptions, history=NO_HISTORY)
            mine.transform(rows, resolutions=res, history=NO_HISTORY)
    assert held == [None, None]


WRITES = {**CHOSEN, "writer": Chosen("newest", WRITER_NEWEST.chooser)}
"""The test choosers, the heartbeat writer read as the newest live worker's."""

BORN_020 = Entry(
    run_id="r",
    from_format="0.2.0",
    to_format="0.3.0",
    runstate="x",
    step_source="sha256:0",
    at=0.0,
    limits=LIMITS,
    assumed=None,
    facts=None,
    resolved={},
    readings={},
    accepted={},
)


class _NotTheLatest:
    """A Writer chooser that reads a heartbeat whose writer is open as the earlier
    worker's: claim 3 did not write it."""

    def choose(self, question):
        if question.key.subject == 3:
            (a,) = [a for a in question.answers if a.value == ()]
            return a
        return WRITER_NEWEST.chooser.choose(question)


def _two_live_workers_beat():
    """Claim 1's worker alive past claim 3's takeover, and a beacon at seq 4 naming
    claim 3; and the statement that admits it, its birth unstated."""
    rows = _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
            r(3, "lifecycle.started", _started("local://n1/12", 3.0)),
            r(4, "lifecycle.heartbeat", _beat(None, 0, 3, 4.0)),
            r(5, "lifecycle.heartbeat", _beat(0, 0, 3, 5.0)),
        ]
    )
    live = REFERENCE._replace(
        workers={
            "ref": dataclasses.replace(
                REFERENCE.workers["ref"], takeover=frozenset(V.Takeover)
            )
        },
        third_parties={},
        born=None,
    )
    return rows, live


def test_who_wrote_a_heartbeat_in_a_log_born_in_0_2_0_is_the_writer_projection():
    """0.4.0 writes a heartbeat's writer as its ``claim_seq``, which ``await_consumed``
    and the Watcher read; in a log born in 0.2.0 any live worker may have written it.
    With claim 1's worker alive past claim 3's takeover, the beacon at seq 4 is either
    one's: a Writer fork for each claim, naming ``takeover`` as the fact that settles
    it, which ``newest`` reads as the newest worker's and a consumer's chooser may read
    otherwise. Span reads no heartbeat, and its built-ins never see one. The birth is
    the manifest's evidence, the consumer stating none."""
    rows, live = _two_live_workers_beat()
    found = step.detect(
        rows,
        assumptions=under(live, **WRITES).assumptions,
        history=History((BORN_020,), ()),
    ).forks
    assert [f.seq for f in found["writer"]] == [4, 4]  # claim 3's and claim 1's
    assert all("takeover" in f.settled_by for f in found["writer"])
    assert not any(
        isinstance(p, E.PBeat)
        for q in E.questions(
            E.candidates(
                rows, live, born=V.Born020(), cap=3000, budget=6_000_000, run_on=2
            ),
            cap=3000,
        )
        if q.key.projection is not E.Projection.WRITER
        for a in q.answers
        for p in a.value
    )

    def beacon(**choosers):
        m = step.transform(
            rows, resolutions=under(live, **choosers), history=History((BORN_020,), ())
        )
        (b,) = [x for x in m.rows if x.topic == "lifecycle.heartbeat"][:1]
        return json.loads(b.body)["claim_seq"], m.rows

    named, out = beacon(**WRITES)
    assert named == [x.seq for x in out if x.topic == "lifecycle.started"][1]
    named, out = beacon(**{**WRITES, "writer": Chosen("x", _NotTheLatest())})
    assert named == [x.seq for x in out if x.topic == "lifecycle.started"][0]
    with pytest.raises(MigrationError, match="writer"):
        beacon(**CHOSEN)  # no --resolve decides writer


def _writer_forks(rows, configuration, history):
    a = under(configuration, **WRITES).assumptions
    return [
        f.seq for f in step.detect(rows, assumptions=a, history=history).forks["writer"]
    ]


def _beacon_names(rows, configuration, history):
    m = step.transform(
        rows, resolutions=under(configuration, **WRITES), history=history
    )
    starts = [x.seq for x in m.rows if x.topic == "lifecycle.started"]
    (b,) = [x for x in m.rows if x.topic == "lifecycle.heartbeat"][:1]
    return starts.index(json.loads(b.body)["claim_seq"])


def test_a_log_no_evidence_dates_is_refused_unless_its_birth_is_stated():
    """The review's pair, under every statement. With no manifest entry and no trace,
    silence decides nothing: unstated, or stated empty, the run is refused, the refusal
    naming the statement. ``{0.3.0}`` takes the beacon's name at its word, so no fork;
    ``{0.2.0}`` and both, the wider reading, read it as an inference, so the writer
    forks, as the manifest's evidence makes it."""
    rows, live = _two_live_workers_beat()
    for born in (None, frozenset()):
        cfg = live._replace(born=born)
        for run in (_writer_forks, _beacon_names):
            with pytest.raises(MigrationError, match=r"Configuration\.born") as e:
                run(rows, cfg, NO_HISTORY)
            assert "nothing shows where its log was born" in str(e.value)
    at_its_word = live._replace(born=frozenset({V.Birth.V0_3_0}))
    assert _writer_forks(rows, at_its_word, NO_HISTORY) == []
    assert _beacon_names(rows, at_its_word, NO_HISTORY) == 1  # claim 3, as it says
    inferred = _writer_forks(rows, live, History((BORN_020,), ()))
    assert inferred == [4, 4]
    for born in ({V.Birth.V0_2_0}, set(V.Birth)):
        cfg = live._replace(born=frozenset(born))
        assert _writer_forks(rows, cfg, NO_HISTORY) == inferred
        assert _beacon_names(rows, cfg, NO_HISTORY) == 1  # ``newest``: claim 3


def test_evidence_of_a_birth_decides_it_and_refuses_a_statement_it_contradicts():
    """The manifest's 0.2.0 -> 0.3.0 entry and the store's trace of the log in 0.2.0
    each show a birth in 0.2.0, whatever is stated, unless the statement leaves 0.2.0
    out: the run is then refused, naming the evidence and the statement."""
    rows, live = _two_live_workers_beat()
    sealed = Trace("0.2.0", "a sealed log at v0.2.0/r.db")
    tombstone = Trace("0.2.0", "a legacy tombstone at r.db")
    for history in (
        History((BORN_020,), ()),
        History((), (sealed,)),
        History((), (tombstone,)),
    ):
        for born in (None, frozenset({V.Birth.V0_2_0}), frozenset(V.Birth)):
            assert _writer_forks(rows, live._replace(born=born), history) == [4, 4]
        shows = history.traces[0].where if history.traces else "manifest entry"
        for born in (frozenset({V.Birth.V0_3_0}), frozenset()):
            with pytest.raises(MigrationError, match="born in format 0.2.0") as e:
                _writer_forks(rows, live._replace(born=born), history)
            assert shows in str(e.value) and "Configuration.born" in str(e.value)
    # a trace of the log in its own format, sealed by a failed migration, is no birth
    own = History((), (Trace("0.3.0", "a sealed log at v0.3.0/r.db"),))
    assert (
        _writer_forks(rows, live._replace(born=frozenset({V.Birth.V0_3_0})), own) == []
    )


def _dead_workers_beat():
    """``_two_live_workers_beat``'s log, its workers' handles dead now, so the runner
    lets the step read it."""
    rows, live = _two_live_workers_beat()
    other = DEAD[: -len("2147483646")] + "2147483645"
    handles = {1: DEAD, 3: other}
    return [
        (
            x._replace(
                body=json.dumps(_started(handles[x.seq], json.loads(x.body)["t"]))
            )
            if x.topic == "lifecycle.started"
            else x
        )
        for x in rows
    ], live


def _choices_stating(configuration):
    return Choices((Assumed("tests:live", configuration),), {}, frozenset(), LIMITS)


def test_a_sqlite_store_s_sealed_v0_2_0_log_dates_a_birth_the_manifest_does_not(
    registered, tmp_path
):
    """No manifest entry, but the root holds the run's sealed v0.2.0 log: the store's
    trace dates the birth, so the detection pass finds the writer forks, as an entry
    would make it; a statement leaving 0.2.0 out is contradicted. Without the trace,
    and with nothing stated, the run is refused. A legacy tombstone dates it too."""
    rows, live = _dead_workers_beat()
    _seed_0_3_0(tmp_path, "r1", rows)
    store = SqliteStore(tmp_path)
    assert store.history("r1") == NO_HISTORY

    def detected(configuration):
        """The detection pass's report, or the refusal of a run it refused."""
        try:
            out = migrate(
                store, ["r1"], to="0.4.0", choices=_choices_stating(configuration)
            )
        except Undecided as u:
            return u.report
        (refused,) = out.refused
        assert not out.moved and not out.errored
        return refused.message

    assert "nothing shows where its log was born" in detected(live)
    old = FORMATS["0.2.0"].sqlite_path(tmp_path, "r1")
    old.parent.mkdir()
    SqliteChannel(old).close()
    seal_sqlite(old)
    (trace,) = SqliteStore(tmp_path).history("r1").traces
    assert trace == Trace("0.2.0", f"a sealed log at {old}")
    store = SqliteStore(tmp_path)
    report = detected(live)
    ((_, writer),) = [k for k in report.forks if k[1].name == "writer"]
    assert [f.seq for _, f in report.forks[("0.3.0 -> 0.4.0", writer)]] == [4, 4]
    contradicted = detected(live._replace(born=frozenset({V.Birth.V0_3_0})))
    assert f"as a sealed log at {old} shows" in contradicted
    old.unlink()
    tombstone = tmp_path / "r1.db"
    tombstone.touch()
    tombstone.chmod(0o444)
    store = SqliteStore(tmp_path)
    assert store.history("r1").traces == (
        Trace("0.2.0", f"a legacy tombstone at {tombstone}"),
    )
    assert "writer" in {p.name for _, p in detected(live).forks}


def test_a_postgres_store_s_sealed_runs_row_dates_a_birth_the_manifest_does_not(
    pg_ready,
):
    """No manifest entry, but ``runstate_v0_2_0.sealed_runs`` holds the run: the
    store's trace dates the birth, so the step finds the writer forks; without it,
    and with nothing stated, the run is refused."""
    import uuid

    import psycopg
    from psycopg import sql

    from runstate.channel.postgres import ensure_schema
    from runstate.migrations.seal import seal_postgres
    from runstate.migrations.stores import PostgresStore

    rows, live = _dead_workers_beat()
    rid = f"born-{uuid.uuid4().hex}"
    ensure_schema(pg_ready)
    new = FORMATS["0.3.0"].pg_schema()
    with psycopg.connect(pg_ready) as c, c.transaction():
        for x in rows:
            c.execute(
                sql.SQL(
                    "INSERT INTO {}.log (run_id, seq, topic, name, request_id, body,"
                    " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                ).format(sql.Identifier(new)),
                [rid, *x],
            )
    store = PostgresStore(pg_ready)
    history = store.history(rid)
    assert history == NO_HISTORY
    with pytest.raises(MigrationError, match="nothing shows where its log was born"):
        _writer_forks(rows, live, history)
    old = FORMATS["0.2.0"].pg_schema()
    with psycopg.connect(pg_ready) as c, c.transaction():
        c.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(old)))
        c.execute(
            sql.SQL("CREATE TABLE IF NOT EXISTS {}.log (LIKE {}.log)").format(
                sql.Identifier(old), sql.Identifier(new)
            )
        )
        seal_postgres(c, old, rid)
    try:
        history = store.history(rid)
        assert history == History((), (Trace("0.2.0", f"a row of {old}.sealed_runs"),))
        assert _writer_forks(rows, live, history) == [4, 4]
    finally:
        with psycopg.connect(pg_ready) as c, c.transaction():
            for schema in (old, new):
                table = "sealed_runs" if schema == old else "log"
                c.execute(
                    sql.SQL("DELETE FROM {}.{} WHERE run_id = %s").format(
                        sql.Identifier(schema), sql.Identifier(table)
                    ),
                    [rid],
                )


class _FirstAnswer:
    """A Span chooser that takes each fork's first answer: one commit and one rewind
    after pass 0 here."""

    def choose(self, question):
        return question.answers[0]


def test_a_rewinds_place_and_time_are_its_end_time_reading():
    """A rewind is placed as an undated commit is, and no tick dates one: where it
    stands and when are the end-time reading's. Here pass 1 closes, and a rewind to the
    newest save follows it, before the beacon at seq 3 or after it: under ``lower`` the
    rewind is offered both places and written before the beacon at its bound; under
    ``upper`` the commit is written after the beacon, and the rewind with it, at the
    next record's ``t``."""
    rows = [
        r(1, "lifecycle.started", _started("local://n/1", 1.0), t=1.0),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0), t=2.0),
        r(3, "lifecycle.heartbeat", _beat(None, 0, 1, 3.0), t=3.0),
        r(4, "lifecycle.stopped", _stopped(False, "boom", 1, 1, [], 4.0), t=4.0),
    ]
    rewinding = FIX1._replace(
        workers={
            "reference": dataclasses.replace(
                CHECKPOINTED, rewinds=frozenset({V.Rewinds.NEWEST})
            )
        }
    )
    found = step.detect(
        rows, assumptions=under(rewinding, **CHOSEN).assumptions, history=NO_HISTORY
    ).forks
    assert {f.seq for f in found["end-time"]} == {1}
    out, offered = {}, {}
    for builtin in END_TIME.generic:
        asked = _Asked(builtin)
        m = step.transform(
            rows,
            resolutions=under(
                rewinding,
                **{
                    **CHOSEN,
                    "span": Chosen("first", _FirstAnswer()),
                    "end_time": Chosen("x", asked),
                },
            ),
            history=NO_HISTORY,
        )
        (reload,) = [q for q in asked.asked if isinstance(q.act, E.RewindId)]
        assert reload.ticks == ()
        offered[builtin.name] = [u.after for u in reload.unlogged]
        out[builtin.name] = [(x.topic, json.loads(x.body)["t"]) for x in m.rows[2:]]
        assert "rewind 2" in m.readings["end-time"][1]
    assert offered == {"lower": [2, 3], "upper": [3]}
    assert out["lower"] == [
        ("lifecycle.commit", 2.0),
        ("lifecycle.rewind", 2.0),
        ("lifecycle.heartbeat", 3.0),
        ("lifecycle.stopped", 4.0),
    ]
    assert out["upper"] == [
        ("lifecycle.heartbeat", 3.0),
        ("lifecycle.commit", 4.0),
        ("lifecycle.rewind", 4.0),
        ("lifecycle.stopped", 4.0),
    ]


# ----- what the consumer's code decides is the consumer's (fix round 4) ------------------


def test_a_record_before_the_first_pass_is_the_consumers_loading_and_values():
    """Consumer code around the reference Worker can write a stepped value before the
    first pass (a resumed claim logging the checkpoint it loaded, at that checkpoint's
    step): what its clock reads then (``loading``) and where its values fall
    (``values``) are its word, so the preset takes both, and its classifier answers
    the value every worker role for them to narrow. Under ``loading`` empty the log
    has no history; under ``loaded`` with ``values`` {loading, pass} it migrates, the
    value held by the resumed claim's first commit and read at its count."""
    rows = reference_worker_log("resumed-3-before-2")
    assert [json.loads(x.body)["step"] for x in rows if x.topic == "value"][3] == 2
    with pytest.raises(MigrationError, match=r"^no history .*: relaxing values"):
        step.transform(rows, resolutions=preset(), history=NO_HISTORY)
    with pytest.raises(MigrationError, match=r"relaxing loading; values admits one"):
        step.transform(
            rows,
            resolutions=preset(values=frozenset({V.Phase.LOADING, V.Phase.PASS})),
            history=NO_HISTORY,
        )
    res = preset(
        loading=frozenset({V.Loading.LOADED}),
        values=frozenset({V.Phase.LOADING, V.Phase.PASS}),
    )
    step.detect(rows, assumptions=res.assumptions, history=NO_HISTORY)
    m = step.transform(rows, resolutions=res, history=NO_HISTORY)
    (pre,) = [x for x in m.rows if x.name == "val_loss"]
    first = next(
        x
        for x in m.rows
        if x.topic == "lifecycle.commit" and json.loads(x.body)["claim_seq"] == 9
    )
    assert json.loads(first.body)["values"] == [pre.seq, pre.seq + 1]
    fired = {d: seqs for d, seqs in m.divergences.items() if seqs}
    assert fired == {"recounted-value": (10,)}


def test_a_resumed_claims_first_pass_label_is_the_consumers_stride():
    """``steps(start=k)`` labels a claim's first pass: resuming above the loaded step
    plus one is a skip, the consumer's choice, so ``stride`` is the preset's argument.
    Under ``next`` alone the log has no history, naming the fact; under both it
    migrates."""
    rows = reference_worker_log("resumed-5")
    with pytest.raises(MigrationError, match=r"^no history .*relaxing stride"):
        step.transform(rows, resolutions=preset(), history=NO_HISTORY)
    m = step.transform(
        rows, resolutions=preset(stride=V.every(V.Stride)), history=NO_HISTORY
    )
    assert [x.topic for x in m.rows].count("lifecycle.commit") == 5


def _resume_from(m, claim_seq):
    """The ``resume_from`` of the claim at ``claim_seq``, and the record it names."""
    start = next(x for x in m.rows if x.seq == claim_seq)
    named = json.loads(start.body)["resume_from"]
    node = next(x for x in m.rows if x.seq == named)
    return named, node.topic, json.loads(node.body)["claim_seq"]


def test_a_resume_at_the_loaded_step_is_the_consumers_resume():
    """The Worker's docs resume "from checkpoint step k" with ``steps(start=k)``, and
    what a save labelled ``k`` holds is the consumer's checkpoint code: pass ``k``'s
    result, which ``start=k`` then runs again (``resume`` ``at``), or the state before
    pass ``k`` (``after``). A real Worker log, three passes then a claim loading pass
    2's save and resuming at 2, migrates under each reading alone with that reading's
    ``resume_from``: pass 2's commit under ``at``, pass 1's under ``after``, which must
    then have loaded the older save. Under both the claim's origin forks, which
    detection reports with ``resume`` among the facts that settle it. Under the
    consumer's word that it resumes after the save it loaded, the newest, the log has
    no history, and the refusal names ``resume``."""
    rows = reference_worker_log("resumed-2")
    assert [json.loads(x.body)["step"] for x in rows if x.topic == "value"] == [
        *range(3),
        2,
        3,
    ]
    both = frozenset(V.Resume)
    older = frozenset({V.Load.FRESH, V.Load.NEWEST, V.Load.OLDER})
    with pytest.raises(MigrationError, match=r"^no history .*relaxing .*resume"):
        step.transform(rows, resolutions=preset(), history=NO_HISTORY)
    # at: the newest save, pass 2's result (its tick at seq 7 is its commit), run again
    at = preset(resume=frozenset({V.Resume.AT}))
    assert not any(
        step.detect(rows, assumptions=at.assumptions, history=NO_HISTORY).forks.values()
    )
    m = step.transform(rows, resolutions=at, history=NO_HISTORY)
    assert _resume_from(m, 9) == (7, "lifecycle.commit", 1)
    assert {d: s for d, s in m.divergences.items() if s} == {
        "recounted-value": (10, 12)
    }
    # after: pass 1's save, an older one, continued at 2
    after = preset(load=older)
    assert not any(
        step.detect(
            rows, assumptions=after.assumptions, history=NO_HISTORY
        ).forks.values()
    )
    m = step.transform(rows, resolutions=after, history=NO_HISTORY)
    assert _resume_from(m, 9) == (5, "lifecycle.commit", 1)
    assert {d: s for d, s in m.divergences.items() if s} == {"off-head-value": (6,)}
    # both, with both saves loadable: the origin forks between them
    res = preset(resume=both, load=older)
    forks = step.detect(rows, assumptions=res.assumptions, history=NO_HISTORY).forks
    (origin,) = forks["origin"]
    assert origin.seq == 9 and "resume" in origin.settled_by
    assert [f.seq for f in forks["span"]] == [9]
    assert not forks["end"] and not forks["writer"] and not forks["end-time"]
    m = step.transform(
        rows, resolutions=res, history=NO_HISTORY
    )  # origin=newest: pass 2's
    assert _resume_from(m, 9) == (7, "lifecycle.commit", 1)
    # both, the newest save alone loadable: one history, pass 2's save run again
    res = preset(resume=both)
    assert not any(
        step.detect(
            rows, assumptions=res.assumptions, history=NO_HISTORY
        ).forks.values()
    )
    assert (
        _resume_from(step.transform(rows, resolutions=res, history=NO_HISTORY), 9)[0]
        == 7
    )


class _Picks:
    """A chooser taking each fork's ``i``th answer, from the last where negative."""

    def __init__(self, i):
        self.i = i

    def choose(self, question):
        return question.answers[self.i]


def _a_release_then_a_stopped_naming_the_released_claim():
    """Born in 0.2.0: a third party releases claim 1 at seq 5, fencing its worker; claim 6
    runs; a ``stopped`` naming claim 1 follows at seq 9, which is either the fenced
    worker's own exit or a third party's note. Both write nothing."""
    return [
        r(1, "lifecycle.started", _started("local://n/3", 1.0), t=1.0),
        r(2, "value", _value(1.0, 0, 2.0), name="loss", t=2.0),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 3.0), t=3.0),
        r(4, "control.stop", {}, rid="s0", t=4.0),
        r(5, "lifecycle.stopped", _stopped(False, None, 0, 1, ["s0"], 5.0), t=5.0),
        r(6, "lifecycle.started", _started("local://n/2", 6.0), t=6.0),
        r(7, "value", _value(1.0, 1, 7.0), name="loss", t=7.0),
        r(8, "lifecycle.heartbeat", _beat(1, 4, 6, 8.0), t=8.0),
        r(9, "lifecycle.stopped", _stopped(True, None, 1, 1, ["s0"], 9.0), t=9.0),
    ]


def test_a_stopped_that_writes_nothing_fires_each_divergence_the_paths_left_read():
    """No reading reads who wrote a ``stopped`` that becomes nothing, so after the
    decode a fenced worker's own exit and a third party's note can both remain: the
    rows agree, and ``fenced-exit`` and ``third-party-note`` both fire there, never one
    by the path ``written`` picked."""
    rows = _a_release_then_a_stopped_naming_the_released_claim()
    cfg = V.Configuration(
        {"ref": CHECKPOINTED}, {"tp": UNSTATED.third_parties["any"]}, _Reference(), None
    )
    first = Chosen("first", _Picks(0))
    m = step.transform(
        rows,
        resolutions=under(cfg, **{**WRITES, "end": first, "span": first}),
        history=History((BORN_020,), ()),
    )
    fired = {d: seqs for d, seqs in m.divergences.items() if seqs}
    assert fired["fenced-exit"] == fired["third-party-note"] == (9,)
    assert [x.topic for x in m.rows][-1] == "lifecycle.heartbeat"  # seq 9 leaves


def test_a_writer_fork_names_every_fact_spans_pieces_depend_on():
    """Born in 0.2.0, with ``takeover`` open: claim 1 ticks pass 0, claim 3 claims, and
    a heartbeat with step 1 follows, claim 1's surviving worker's or claim 3's, which
    could tick step 1 only having loaded claim 1's save. Stating ``load`` {fresh}
    settles the fork, so the fork names ``load``: a writer's feeds are Span's (what
    lives, and what its clock reads) and ``watermark``, not a shorter list."""
    rows = _dated(
        [
            r(1, "lifecycle.started", _started("local://n/1", 1.0)),
            r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
            r(3, "lifecycle.started", _started("local://n/2", 3.0)),
            r(4, "lifecycle.heartbeat", _beat(1, 0, 3, 4.0)),
        ]
    )
    assert E.FEEDS[E.Projection.WRITER] == E.FEEDS[E.Projection.SPAN] | {
        E.WorkerFact.WATERMARK
    }
    taken = dataclasses.replace(CHECKPOINTED, takeover=V.every(V.Takeover))

    def forks(f):
        cfg = V.Configuration({"reference": f}, {}, STEP.Reference(), None)
        a = under(cfg, **WRITES).assumptions
        return step.detect(rows, assumptions=a, history=History((BORN_020,), ())).forks[
            "writer"
        ]

    found = forks(taken)
    assert [f.seq for f in found] == [4, 4]
    assert all({"load", "takeover"} <= set(f.settled_by) for f in found)
    assert forks(dataclasses.replace(taken, load=frozenset({V.Load.FRESH}))) == ()


class _Reads:
    """A test classifier: every claim the reference Worker's, every stopped its own, a
    stepped value a pass's or a between value, a stepless one a register there."""

    def read(self, log):
        self._stepless = {
            x.seq
            for x in log
            if x.topic == "value" and json.loads(x.body)["step"] is None
        }
        return self

    def started(self, seq):
        return frozenset({"reference"})

    def stopped(self, seq):
        return frozenset({V.OwnStop()})

    def value(self, seq):
        register = seq in self._stepless
        return frozenset(
            V.ValueRole(x, register) for x in (V.Role.PASS, V.Role.BETWEEN)
        )


def test_two_rewinds_at_one_count_one_to_the_root_write_a_lineage():
    """A claim may rewind twice between the same two commits, once to the root: the
    lineage orders its rewinds by their epoch, not by a target it may lack, and the
    run migrates under either end-time built-in."""
    rows = [
        r(1, "lifecycle.started", _started("local://n/1", 1.0), t=1.0),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0), t=2.0),
        r(3, "value", _value(1.0, 1, 3.0), name="acc", t=3.0),
        r(4, "value", _value(1.0, 1, 4.0), name="loss", t=4.0),
        r(5, "lifecycle.heartbeat", _beat(1, 0, 1, 5.0), t=5.0),
        r(6, "value", _value(3.0, None, 6.0), name="status", t=6.0),
        r(7, "lifecycle.stopped", _stopped(False, "boom", 1, 1, [], 7.0), t=7.0),
        r(8, "lifecycle.started", _started("local://n/3", 8.0), t=8.0),
        r(9, "value", _value(1.0, 2, 9.0), name="acc", t=9.0),
        r(10, "lifecycle.heartbeat", _beat(2, 0, 8, 10.0), t=10.0),
        r(11, "lifecycle.stopped", _stopped(False, None, 2, 8, [], 11.0), t=11.0),
    ]
    rewinding = V.Configuration(
        {
            "reference": dataclasses.replace(
                CHECKPOINTED,
                rewinds=frozenset({V.Rewinds.ROOT, V.Rewinds.NEWEST}),
                values=frozenset({V.Phase.PASS, V.Phase.BETWEEN}),
            )
        },
        {},
        _Reads(),
        frozenset({V.Birth.V0_3_0}),
    )
    for builtin in END_TIME.generic:
        m = step.transform(
            rows,
            resolutions=under(
                rewinding,
                **{
                    **WRITES,
                    "span": Chosen("last", _Picks(-1)),
                    "end_time": Chosen(builtin.name, builtin.chooser),
                },
            ),
            history=NO_HISTORY,
        )
        rewinds = [k for k in m.readings["end-time"][1] if k.startswith("rewind")]
        assert len(rewinds) == 3
        assert [x.topic for x in m.rows].count("lifecycle.rewind") == 3
