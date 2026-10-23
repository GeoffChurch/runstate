"""The 0.3.0 -> 0.4.0 step (commits-and-lineage.md §7; log-formats.md §6).

The golden logs are 0.3.0-shaped by hand, as a 0.3.0 worker and its third
parties wrote them: heartbeats and stoppeds that name their claim, values that
name none. The builders make 0.3.0 bodies (lifecycle-v0.5, value-v0.2); the
0.2.0 step's test module makes 0.2.0 ones.

Each pass is tested directly, on the parsed log. The golden logs' end-to-end
output (seq, topic and body of every row) is asserted once ``transform`` writes
records."""

import json
import socket
from typing import NamedTuple

import pytest

from runstate.formats import FORMATS
from runstate.migrations import RENUMBERING_FROM, STEPS, MigrationError, Row
from runstate.migrations.v0_3_0_to_v0_4_0 import (
    V0_3_0_to_V0_4_0,
    _Malformed,
    _Stopped,
    attribute,
    classify,
    parse,
)

step = V0_3_0_to_V0_4_0()


def r(seq, topic, body, rid=None, name=None, t=0.0):
    return Row(seq, topic, name, rid, json.dumps(body, separators=(",", ":")), t)


DEAD = f"local://{socket.gethostname()}/2147483646"  # this host, above pid_max
BACKSTOP = "subprocess exited 1 with no terminal record"  # mycooc's runner


def _started(handle, t):
    return {"handle": handle, "t": t}


def _beat(step, consumed_seq, claim_seq, t):
    return {"step": step, "consumed_seq": consumed_seq, "claim_seq": claim_seq, "t": t}


def _stopped(completed, error, final_step, claim_seq, honored, t):
    return {
        "completed": completed,
        "error": error,
        "final_step": final_step,
        "claim_seq": claim_seq,
        "honored": honored,
        "t": t,
    }


def _value(value, step, t):
    return {"value": value, "step": step, "t": t}


def _release(reason, honored, claim_seq):
    """The reclaim tool's malformed body (M10), as the 0.2.0 step named it."""
    return {"reason": reason, "honored": honored, "claim_seq": claim_seq}


def _dated(rows):
    """Every record its own created_at, so a test can see each one kept."""
    assert [x.seq for x in rows] == list(range(1, len(rows) + 1))
    return [x._replace(created_at=1000.0 + x.seq / 4) for x in rows]


# ----- golden 0.3.0 logs -----------------------------------------------------------

# A displaced worker's beats keep their claim. A beats to step 1 and stalls; B
# claims, resumes from step 1 and beats 2 and 3, while A, still running, sends
# a value and a beat at step 2, then its own exit. Each beat names its writer's
# claim. A's value names none, so the positional rule gives it to B, as every
# 0.3.0 read did.
DISPLACED = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "value", _value(0.9, 0, 2.0), name="loss"),
        r(4, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(5, "lifecycle.started", _started("local://n2/22", 5.0)),
        r(6, "value", _value(0.8, 2, 6.0), name="loss"),  # A's
        r(7, "lifecycle.heartbeat", _beat(2, 0, 1, 6.0)),  # A's
        r(8, "lifecycle.heartbeat", _beat(2, 0, 5, 7.0)),
        r(9, "value", _value(0.7, 3, 8.0), name="loss"),
        r(10, "lifecycle.heartbeat", _beat(3, 0, 5, 8.0)),
        r(11, "lifecycle.stopped", _stopped(False, None, 2, 1, [], 9.0)),  # A's
        r(12, "lifecycle.stopped", _stopped(True, None, 3, 5, [], 10.0)),
    ]
)

# A worker's own exit, with the stop it honored.
EXIT = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "control.stop", {}, rid="halt"),
        r(4, "lifecycle.stopped", _stopped(False, None, 0, 1, ["halt"], 3.0)),
    ]
)

# The runner's hard-crash backstop ends a claim whose worker wrote no terminal,
# and the "clear staged stop" path then writes a default body naming it (Review
# Focus 4).
BACKSTOPPED = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.stopped", _stopped(False, BACKSTOP, 0, 1, [], 3.0)),
        r(4, "control.stop", {}, rid="staged"),
        r(5, "lifecycle.stopped", _stopped(False, None, 0, 1, ["staged"], 4.0)),
    ]
)

# The backstop is a third party's wherever it falls: here after the worker's
# own exit.
LATE_BACKSTOP = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.stopped", _stopped(False, None, 0, 1, [], 3.0)),
        r(4, "lifecycle.stopped", _stopped(False, BACKSTOP, 0, 1, [], 4.0)),
    ]
)

# The reclaim tool's malformed release (M10), and the repair script's
# well-formed copy of it.
RECLAIMED = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(4, 0, 1, 2.0)),
        r(3, "control.stop", {}, rid="stop@3"),
        r(4, "lifecycle.stopped", _release("reclaimed: slurm TIMEOUT", ["stop@3"], 1)),
        r(5, "lifecycle.stopped", _stopped(False, None, None, 1, [], 5.0)),
    ]
)

# A third party's release of a run nothing claimed (mycooc's resume_fanout).
CLAIMLESS = _dated(
    [
        r(1, "control.stop", {}, rid="pre"),
        r(2, "lifecycle.stopped", _stopped(False, None, None, None, ["pre"], 1.0)),
    ]
)


# ----- pass 1: attribution -----------------------------------------------------------


def _beats_and_values(rows):
    """``{claim seq: (beat seqs, value seqs)}`` and the unclaimed value seqs."""
    out = attribute(parse(rows))
    claims = {
        c.started.seq: ([b.seq for b in c.beats], [v.seq for v in c.values])
        for c in out.claims
    }
    return claims, [v.seq for v in out.unclaimed]


def test_a_displaced_workers_beats_keep_their_claim():
    """A heartbeat belongs to the claim its claim_seq names, wherever it lands; a
    value belongs to the latest claim before it, positionally."""
    claims, unclaimed = _beats_and_values(DISPLACED)
    assert claims == {1: ([2, 4, 7], [3]), 5: ([8, 10], [6, 9])}
    assert unclaimed == []


def test_claims_are_in_claim_order():
    assert [c.started.seq for c in attribute(parse(DISPLACED)).claims] == [1, 5]


def test_a_value_before_every_claim_belongs_to_none():
    rows = [
        r(1, "value", _value({"lr": 0.1}, None, None), name="config"),
        r(2, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(3, "value", _value(0.5, 0, 2.0), name="loss"),
    ]
    assert _beats_and_values(rows) == ({2: ([], [3])}, [1])


def test_attribution_reads_the_typed_bodies():
    claim = attribute(parse(DISPLACED)).claims[0]
    assert claim.started.handle == "local://n1/11" and claim.started.t == 1.0
    beat = claim.beats[2]
    assert (beat.step, beat.consumed_seq, beat.claim_seq, beat.t) == (2, 0, 1, 6.0)
    value = claim.values[0]
    assert (value.value, value.step, value.t) == (0.9, 0, 2.0)


# ----- pass 2: classification ----------------------------------------------------------


class Classes(NamedTuple):
    """A classification by seqs: each class's stops, as a test reads them."""

    exits: dict[int, int]
    releases: dict[int, int]
    later: list[int]
    claimless: list[int]


def _classes(rows):
    c = classify(parse(rows))
    return Classes(
        exits={claim: s.seq for claim, s in c.exits.items()},
        releases={claim: s.seq for claim, s in c.releases.items()},
        later=[s.seq for s in c.later],
        claimless=[s.seq for s in c.claimless],
    )


def test_the_first_well_formed_stopped_naming_a_claim_is_its_exit():
    assert _classes(EXIT) == Classes(exits={1: 4}, releases={}, later=[], claimless=[])


def test_a_displaced_workers_exit_ends_its_own_claim():
    assert _classes(DISPLACED) == Classes(
        exits={1: 11, 5: 12}, releases={}, later=[], claimless=[]
    )


def test_the_backstop_ends_its_claim_as_a_third_partys():
    """The backstop is identified by its text; the default body after it is a
    third party's later stop."""
    assert _classes(BACKSTOPPED) == Classes(
        exits={}, releases={1: 3}, later=[5], claimless=[]
    )


@pytest.mark.parametrize("code", ["1", "-9", "137"])
def test_the_backstop_is_known_by_its_text_whatever_the_code(code):
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(
            2,
            "lifecycle.stopped",
            _stopped(
                False, f"subprocess exited {code} with no terminal record", 0, 1, [], 2
            ),
        ),
    ]
    assert _classes(rows).releases == {1: 2}


@pytest.mark.parametrize(
    "error",
    [
        "boom",
        "subprocess exited with no terminal record",
        "subprocess exited 1 with no terminal record (again)",
        "worker: subprocess exited 1 with no terminal record",
    ],
)
def test_an_error_exit_that_is_not_the_backstop_is_the_workers(error):
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.stopped", _stopped(False, error, 0, 1, [], 2.0)),
    ]
    assert _classes(rows).exits == {1: 2}


def test_the_backstop_after_an_exit_is_a_later_third_partys():
    assert _classes(LATE_BACKSTOP) == Classes(
        exits={1: 3}, releases={}, later=[4], claimless=[]
    )


def test_a_malformed_release_ends_its_claim_and_its_repair_is_later():
    """M10: the reclaim tool's body is a third party's release, and counts as the
    first stopped naming its claim."""
    assert _classes(RECLAIMED) == Classes(
        exits={}, releases={1: 4}, later=[5], claimless=[]
    )


def test_a_stopped_naming_no_claim_is_claimless():
    assert _classes(CLAIMLESS) == Classes(
        exits={}, releases={}, later=[], claimless=[2]
    )


def test_a_malformed_release_naming_no_claim_is_claimless():
    rows = [r(1, "lifecycle.stopped", _release("x", [], None))]
    assert _classes(rows).claimless == [1]


def test_every_stop_is_in_exactly_one_class():
    log = DISPLACED + [
        r(13, "lifecycle.stopped", _stopped(False, None, 3, 5, [], 11.0)),
        r(14, "lifecycle.stopped", _stopped(False, BACKSTOP, 3, 1, [], 12.0)),
        r(15, "lifecycle.stopped", _release("x", [], 5)),
    ]
    assert _classes(log) == Classes(
        exits={1: 11, 5: 12}, releases={}, later=[13, 14, 15], claimless=[]
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
        step.transform(rows)


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
        r(2, topic, body),
    ]
    with pytest.raises(MigrationError, match=rf"{topic} at seq 2 "):
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


def test_records_the_step_does_not_interpret_pass_the_boundary_as_they_are():
    rows = [
        r(1, "user.note", {"any": ["shape"]}, name="n"),
        r(2, "control.stop", {"from": {"bogus": 1}}, rid="bad"),
    ]
    log = parse(_dated(rows))
    assert [
        (x.seq, x.topic, x.name, x.request_id, x.body, x.created_at)
        for x in log.records
    ] == [
        (1, "user.note", "n", None, {"any": ["shape"]}, 1000.25),
        (2, "control.stop", None, "bad", {"from": {"bogus": 1}}, 1000.5),
    ]


def test_transform_runs_the_passes_it_has_and_writes_nothing_yet():
    with pytest.raises(NotImplementedError, match="pass 3"):
        step.transform(DISPLACED)


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


def test_a_claim_without_a_handle_cannot_be_judged():
    with pytest.raises(MigrationError, match="seq 1"):
        step.is_live([r(1, "lifecycle.started", {"t": 0.0})])


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
    it takes only Row, MigrationError and resolve(), an OS probe of a handle
    rather than a format rule, and never runstate.lineage."""
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
    assert internal == {
        (1, None, "MigrationError"),
        (1, None, "Row"),
        (2, "vocabulary.handle", "resolve"),
    }
    plain = {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert not {m for m in plain if m.startswith("runstate")}
