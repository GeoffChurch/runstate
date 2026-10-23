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
    Classification,
    ExitNode,
    Place,
    Real,
    Synthetic,
    Uncommitted,
    V0_3_0_to_V0_4_0,
    _Exit,
    _Malformed,
    _newest_reaching,
    _Release,
    _Stopped,
    _Unattributed,
    attribute,
    classify,
    parse,
    synthesize,
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

LOADING_STATUS = {"phase": "LOADING", "status": "loading enwiki"}

# A step-0 loading status (mycooc's): the worker mirrors its status at step 0
# while it loads, before its first beat. A fresh claim's is committed by its
# step-0 commit (M2). A resumed claim's is a leading value, below its first real
# beat, and goes to its first commit: nothing is synthesized below that beat.
LOADING = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(LOADING_STATUS, 0, 1.5), name="status"),
        r(3, "value", _value(0.9, 0, 2.0), name="loss"),
        r(4, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(5, "value", _value(0.8, 1, 3.0), name="loss"),
        r(6, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(7, "lifecycle.stopped", _stopped(False, None, 1, 1, [], 4.0)),
        r(8, "lifecycle.started", _started("local://n1/12", 5.0)),
        r(9, "value", _value(LOADING_STATUS, 0, 5.5), name="status"),
        r(10, "value", _value(0.7, 2, 6.0), name="loss"),
        r(11, "lifecycle.heartbeat", _beat(2, 0, 8, 6.0)),
        r(12, "lifecycle.stopped", _stopped(True, None, 2, 8, [], 7.0)),
    ]
)

# A skipped tick, with values and without: the worker beat 0, 1, 3 and 5. Step
# 2's tick was skipped after its values were written, and step 4's with none
# written. M5 synthesizes both, since Commits() must count them. M8 dates step
# 2's by its last value that carries a t, and step 4's by the beat before it.
SKIPPED = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(1.0, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "value", _value(0.9, 1, 3.0), name="loss"),
        r(5, "lifecycle.heartbeat", _beat(1, 4, 1, 3.0)),
        r(6, "value", _value(0.8, 2, 4.0), name="loss"),
        r(7, "value", _value(0.5, 2, None), name="acc"),  # undated, as translation's
        r(8, "value", _value(0.7, 3, 5.0), name="loss"),
        r(9, "lifecycle.heartbeat", _beat(3, 8, 1, 5.0)),
        r(10, "value", _value(0.5, 5, 7.0), name="loss"),
        r(11, "lifecycle.heartbeat", _beat(5, 10, 1, 7.0)),
        r(12, "lifecycle.stopped", _stopped(True, None, 5, 1, [], 8.0)),
    ]
)

# Post-loop values: after the loop's last tick the worker writes a final metric,
# stamped with the last step, and its completion reason, a register. The step
# label wins (M2): the final metric is the last commit's, though it follows that
# beat. The register is stepless and stays outside every lineage (M3).
POST_LOOP = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "value", _value(0.8, 1, 3.0), name="loss"),
        r(5, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(6, "value", _value(0.95, 1, 4.0), name="final_eval"),
        r(7, "value", _value({"reason": "max_steps"}, None, 4.5), name="reason"),
        r(8, "lifecycle.stopped", _stopped(True, None, 1, 1, [], 5.0)),
    ]
)

# A trailing step before a non-error exit (M5's second case): the worker wrote
# step 2's values, then completed inside the iteration, so the exit's last step
# is 2. The exit vouches for its iteration: step 2 is synthesized, and the exit
# holds nothing.
TRAILING = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "value", _value(0.8, 1, 3.0), name="loss"),
        r(5, "lifecycle.heartbeat", _beat(1, 4, 1, 3.0)),
        r(6, "value", _value(0.7, 2, 4.0), name="loss"),
        r(7, "lifecycle.stopped", _stopped(True, None, 2, 1, [], 5.0)),
    ]
)

# A preempted exit is a non-error exit: its trailing step is synthesized too.
PREEMPTED = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "value", _value(0.8, 1, 3.0), name="loss"),
        r(5, "lifecycle.stopped", _stopped(False, None, 1, 1, [], 4.0)),
    ]
)

# An errored exit discards its open iteration: it holds null, and its trailing
# step, which no later claim resumed from, stays uncommitted.
ERRORED = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "value", _value(0.8, 1, 3.0), name="loss"),
        r(5, "lifecycle.stopped", _stopped(False, "CUDA out of memory", 1, 1, [], 4.0)),
    ]
)

# M5's third case past a retry that crashed while loading (Review Focus 3). A
# crashed in step 2 after writing its values. B, a retry, crashed while loading
# and beat nothing. C resumed from step 2's checkpoint: its first real beat is
# 3. A is the newest claim before C to reach step 2, by trailing values, so A's
# step 2 is synthesized, though A's exit is an error.
RESUMED_PAST_A_LOADING_CRASH = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "value", _value(0.8, 1, 3.0), name="loss"),
        r(5, "lifecycle.heartbeat", _beat(1, 4, 1, 3.0)),
        r(6, "value", _value(0.7, 2, 4.0), name="loss"),
        r(7, "lifecycle.stopped", _stopped(False, "CUDA out of memory", 2, 1, [], 5.0)),
        r(8, "lifecycle.started", _started("local://n1/12", 6.0)),
        r(9, "value", _value(LOADING_STATUS, None, 6.5), name="status"),
        r(10, "lifecycle.stopped", _stopped(False, "OSError", None, 8, [], 7.0)),
        r(11, "lifecycle.started", _started("local://n1/13", 8.0)),
        r(12, "value", _value(0.6, 3, 9.0), name="loss"),
        r(13, "lifecycle.heartbeat", _beat(3, 0, 11, 9.0)),
        r(14, "lifecycle.stopped", _stopped(True, None, 3, 11, [], 10.0)),
    ]
)

# M5's third case beside a newer claim that reached the step by a real beat
# (Review Focus 3). A crashed in step 2 after writing its values. B resumed from
# step 1, beat 2 and was preempted. C resumed from step 2: its first real beat
# is 3. B is the newest claim before C to reach step 2, by a commit, so nothing
# is synthesized, and A's step-2 values stay uncommitted: nothing says A's
# iteration finished.
RESUMED_BESIDE_A_REAL_BEAT = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "value", _value(0.8, 1, 3.0), name="loss"),
        r(5, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(6, "value", _value(0.7, 2, 4.0), name="loss"),
        r(7, "lifecycle.stopped", _stopped(False, "CUDA out of memory", 2, 1, [], 5.0)),
        r(8, "lifecycle.started", _started("local://n1/12", 6.0)),
        r(9, "value", _value(0.71, 2, 7.0), name="loss"),
        r(10, "lifecycle.heartbeat", _beat(2, 0, 8, 7.0)),
        r(11, "lifecycle.stopped", _stopped(False, None, 2, 8, [], 8.0)),
        r(12, "lifecycle.started", _started("local://n1/13", 9.0)),
        r(13, "value", _value(0.6, 3, 10.0), name="loss"),
        r(14, "lifecycle.heartbeat", _beat(3, 0, 12, 10.0)),
        r(15, "lifecycle.stopped", _stopped(True, None, 3, 12, [], 11.0)),
    ]
)

# Values after their claim's stopped (M4): the teardown status the worker wrote
# after its exit, at a step a real beat carries, stays uncommitted; its stepless
# completion reason is M3's first.
AFTER_STOPPED = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "lifecycle.stopped", _stopped(True, None, 0, 1, [], 3.0)),
        r(
            5,
            "value",
            _value({"phase": "DONE", "status": "done"}, 0, 4.0),
            name="status",
        ),
        r(6, "value", _value({"reason": "max_steps"}, None, 4.5), name="reason"),
    ]
)

# A displaced worker's value above its successor's exit's last step: A, still
# running after B claimed, beats 2 and writes step 3's value, which pass 1 gives
# B. B's exit's last step is 2, so the value stays uncommitted: B's worker never
# reached step 3.
ABOVE = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(4, "lifecycle.started", _started("local://n2/22", 4.0)),
        r(5, "lifecycle.heartbeat", _beat(2, 0, 4, 5.0)),
        r(6, "lifecycle.heartbeat", _beat(2, 0, 1, 5.5)),  # A's
        r(7, "value", _value(0.5, 3, 6.0), name="loss"),  # A's
        r(8, "lifecycle.stopped", _stopped(True, None, 2, 4, [], 7.0)),
        r(9, "lifecycle.stopped", _stopped(False, None, 3, 1, [], 8.0)),  # A's
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
    """A classification by seqs, as a test reads it: each claim's end as its
    variant and its stop's seq, each later stop as its claim and seq, and each
    claimless stop's seq."""

    ends: dict[int, tuple[type, int]]
    later: list[tuple[int, int]]
    claimless: list[int]


def _classes(rows):
    c = classify(parse(rows))
    return Classes(
        ends={claim: (type(end), end.stop.seq) for claim, end in c.ends.items()},
        later=[(x.claim, x.stop.seq) for x in c.later],
        claimless=[s.seq for s in c.claimless],
    )


def test_the_first_well_formed_stopped_naming_a_claim_is_its_exit():
    assert _classes(EXIT) == Classes(ends={1: (_Exit, 4)}, later=[], claimless=[])


def test_a_displaced_workers_exit_ends_its_own_claim():
    assert _classes(DISPLACED) == Classes(
        ends={1: (_Exit, 11), 5: (_Exit, 12)}, later=[], claimless=[]
    )


def test_the_backstop_ends_its_claim_as_a_third_partys():
    """The backstop is identified by its text; the default body after it is a
    third party's later stop."""
    assert _classes(BACKSTOPPED) == Classes(
        ends={1: (_Release, 3)}, later=[(1, 5)], claimless=[]
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
    assert _classes(rows).ends == {1: (_Release, 2)}


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
    assert _classes(rows).ends == {1: (_Exit, 2)}


def test_the_backstop_after_an_exit_is_a_later_third_partys():
    assert _classes(LATE_BACKSTOP) == Classes(
        ends={1: (_Exit, 3)}, later=[(1, 4)], claimless=[]
    )


def test_a_malformed_release_ends_its_claim_and_its_repair_is_later():
    """M10: the reclaim tool's body is a third party's release, and counts as the
    first stopped naming its claim."""
    assert _classes(RECLAIMED) == Classes(
        ends={1: (_Release, 4)}, later=[(1, 5)], claimless=[]
    )


def test_a_stopped_naming_no_claim_is_claimless():
    assert _classes(CLAIMLESS) == Classes(ends={}, later=[], claimless=[2])


def test_a_malformed_release_naming_no_claim_is_claimless():
    rows = [r(1, "lifecycle.stopped", _release("x", [], None))]
    assert _classes(rows).claimless == [1]


def test_a_backstop_naming_no_claim_is_claimless():
    """It becomes withdrawals only: a died must name a claim (supervisor-v0.5's
    required integer claim_seq), whoever wrote it."""
    rows = [
        r(1, "control.stop", {}, rid="pre"),
        r(2, "lifecycle.stopped", _stopped(False, BACKSTOP, None, None, ["pre"], 1.0)),
        r(3, "lifecycle.started", _started("local://n1/11", 2.0)),
    ]
    assert _classes(rows) == Classes(ends={}, later=[], claimless=[2])


def test_every_stop_is_in_exactly_one_class():
    log = DISPLACED + [
        r(13, "lifecycle.stopped", _stopped(False, None, 3, 5, [], 11.0)),
        r(14, "lifecycle.stopped", _stopped(False, BACKSTOP, 3, 1, [], 12.0)),
        r(15, "lifecycle.stopped", _release("x", [], 5)),
    ]
    assert _classes(log) == Classes(
        ends={1: (_Exit, 11), 5: (_Exit, 12)},
        later=[(5, 13), (1, 14), (5, 15)],
        claimless=[],
    )


GOLDEN = {
    "displaced": DISPLACED,
    "exit": EXIT,
    "backstopped": BACKSTOPPED,
    "late_backstop": LATE_BACKSTOP,
    "reclaimed": RECLAIMED,
    "claimless": CLAIMLESS,
    "loading": LOADING,
    "skipped": SKIPPED,
    "post_loop": POST_LOOP,
    "trailing": TRAILING,
    "preempted": PREEMPTED,
    "errored": ERRORED,
    "resumed_past_a_loading_crash": RESUMED_PAST_A_LOADING_CRASH,
    "resumed_beside_a_real_beat": RESUMED_BESIDE_A_REAL_BEAT,
    "after_stopped": AFTER_STOPPED,
    "above": ABOVE,
}


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_every_stop_is_classified_once_and_every_claim_ends_at_most_once(rid):
    """The classified seqs are exactly the stop seqs, each once; an end and a
    later stop name the claim they are filed under; and an end is the first
    stop naming its claim, every later one after it."""
    rows = GOLDEN[rid]
    log = parse(rows)
    c = classify(log)
    stops = [x.seq for x in rows if x.topic == "lifecycle.stopped"]
    ends = [end.stop.seq for end in c.ends.values()]
    later = [x.stop.seq for x in c.later]
    claimless = [s.seq for s in c.claimless]
    assert sorted(ends + later + claimless) == stops
    assert set(c.ends) <= {claim.seq for claim in log.claims}
    assert all(end.stop.claim_seq == claim for claim, end in c.ends.items())
    assert all(x.stop.claim_seq == x.claim for x in c.later)
    assert all(c.ends[x.claim].stop.seq < x.stop.seq for x in c.later)
    assert all(s.claim_seq is None for s in c.claimless)


# ----- pass 3: synthesis -----------------------------------------------------------------

SKIP, CLEAN, RESUMED = Place.SKIPPED_TICK, Place.BEFORE_A_CLEAN_EXIT, Place.RESUMED_FROM


class Synth(NamedTuple):
    """A synthesis by seqs, as a test reads it: each claim's commits as (old
    step, origin, value seqs), the origin a real commit's heartbeat seq or a
    synthetic one's place; each exit node's values; and each uncommitted value's
    reason."""

    commits: dict[int, list[tuple[int, int | Place, list[int]]]]
    exits: dict[int, tuple[()] | None]
    left: dict[int, Uncommitted]


def _synthesis(rows, classification=None):
    log = parse(rows)
    return synthesize(
        attribute(log), classify(log) if classification is None else classification
    )


def _origin(commit):
    if isinstance(commit.origin, Real):
        return commit.origin.beat.seq
    return commit.origin.place


def _synth(rows, classification=None):
    s = _synthesis(rows, classification)
    return Synth(
        commits={
            c.claim.started.seq: [
                (k.step, _origin(k), list(k.values)) for k in c.commits
            ]
            for c in s.claims
        },
        exits={
            c.claim.started.seq: c.exit.values for c in s.claims if c.exit is not None
        },
        left={x.value.seq: x.why for x in s.uncommitted},
    )


def test_a_step_0_loading_status_is_a_resumed_claims_leading_value():
    assert _synth(LOADING) == Synth(
        commits={1: [(0, 4, [2, 3]), (1, 6, [5])], 8: [(2, 11, [9, 10])]},
        exits={1: (), 8: ()},
        left={},
    )


def test_a_skipped_tick_is_synthesized_with_its_values_and_without():
    assert _synth(SKIPPED) == Synth(
        commits={
            1: [
                (0, 3, [2]),
                (1, 5, [4]),
                (2, SKIP, [6, 7]),
                (3, 9, [8]),
                (4, SKIP, []),
                (5, 11, [10]),
            ]
        },
        exits={1: ()},
        left={},
    )


def test_post_loop_values_go_to_the_commit_their_step_names():
    assert _synth(POST_LOOP) == Synth(
        commits={1: [(0, 3, [2]), (1, 5, [4, 6])]},
        exits={1: ()},
        left={7: Uncommitted.STEPLESS},
    )


def test_a_trailing_step_before_a_completed_exit_is_synthesized():
    assert _synth(TRAILING) == Synth(
        commits={1: [(0, 3, [2]), (1, 5, [4]), (2, CLEAN, [6])]},
        exits={1: ()},
        left={},
    )


def test_a_trailing_step_before_a_preempted_exit_is_synthesized():
    assert _synth(PREEMPTED) == Synth(
        commits={1: [(0, 3, [2]), (1, CLEAN, [4])]},
        exits={1: ()},
        left={},
    )


def test_an_errored_exit_holds_null_and_its_trailing_step_stays_uncommitted():
    assert _synth(ERRORED) == Synth(
        commits={1: [(0, 3, [2])]},
        exits={1: None},
        left={4: Uncommitted.UNREACHED},
    )


def test_m5s_third_case_reaches_past_a_retry_that_crashed_while_loading():
    """Review Focus 3: the later claim is any, not only the next."""
    assert _synth(RESUMED_PAST_A_LOADING_CRASH) == Synth(
        commits={
            1: [(0, 3, [2]), (1, 5, [4]), (2, RESUMED, [6])],
            8: [],
            11: [(3, 13, [12])],
        },
        exits={1: None, 8: None, 11: ()},
        left={9: Uncommitted.STEPLESS},
    )


def test_m5s_third_case_synthesizes_nothing_beside_a_newer_claims_real_beat():
    """Review Focus 3: only in the claim M1 will choose, the newest before the
    resuming claim to reach the step."""
    assert _synth(RESUMED_BESIDE_A_REAL_BEAT) == Synth(
        commits={
            1: [(0, 3, [2]), (1, 5, [4])],
            8: [(2, 10, [9])],
            12: [(3, 14, [13])],
        },
        exits={1: None, 8: (), 12: ()},
        left={6: Uncommitted.UNREACHED},
    )


def test_values_after_their_claims_stopped_stay_uncommitted():
    assert _synth(AFTER_STOPPED) == Synth(
        commits={1: [(0, 3, [2])]},
        exits={1: ()},
        left={5: Uncommitted.AFTER_ITS_END, 6: Uncommitted.STEPLESS},
    )


def test_a_displaced_workers_value_above_its_successors_exit_stays_uncommitted():
    assert _synth(ABOVE) == Synth(
        commits={1: [(0, 2, []), (1, 3, []), (2, 6, [])], 4: [(2, 5, [])]},
        exits={1: (), 4: ()},
        left={7: Uncommitted.ABOVE_THE_LAST_STEP},
    )


def test_a_value_before_every_claim_is_left_and_a_register_is_m3s_first():
    rows = [
        r(1, "value", _value({"lr": 0.1}, None, None), name="config"),
        r(2, "value", _value(0.9, 0, 0.5), name="loss"),
        r(3, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(4, "lifecycle.heartbeat", _beat(0, 0, 3, 2.0)),
    ]
    assert _synth(rows) == Synth(
        commits={3: [(0, 4, [])]},
        exits={},
        left={1: Uncommitted.STEPLESS, 2: Uncommitted.BEFORE_EVERY_CLAIM},
    )


def test_m2_reads_only_the_values_claims_own_commits():
    """A's step-2 value, which pass 1 gives B, is B's step-2 commit's, though A
    beat step 2 too."""
    assert _synth(DISPLACED).commits == {
        1: [(0, 2, [3]), (1, 4, []), (2, 7, [])],
        5: [(2, 8, [6]), (3, 10, [9])],
    }


def test_a_third_partys_release_is_no_exit_node():
    assert _synth(BACKSTOPPED) == Synth(commits={1: [(0, 2, [])]}, exits={}, left={})
    assert _synth(RECLAIMED) == Synth(commits={1: [(4, 2, [])]}, exits={}, left={})


def _crashed_in_step_1(end):
    """A beat step 0, wrote step 1's values and ended as ``end`` says."""
    return [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "value", _value(0.8, 1, 3.0), name="loss"),
        *end,
    ]


BACKSTOP_AT_5 = [r(5, "lifecycle.stopped", _stopped(False, BACKSTOP, 0, 1, [], 4.0))]
NO_END = [r(5, "user.note", {"killed": True})]


@pytest.mark.parametrize("end", [BACKSTOP_AT_5, NO_END], ids=["released", "no_end"])
def test_m5s_third_case_reaches_a_claim_whatever_ended_it(end):
    """'Whatever the exit': B resumed from step 1, so A's step 1 finished,
    though no exit of A's vouches for it."""
    rows = _crashed_in_step_1(end) + [
        r(6, "lifecycle.started", _started("local://n1/12", 5.0)),
        r(7, "lifecycle.heartbeat", _beat(2, 0, 6, 6.0)),
        r(8, "lifecycle.stopped", _stopped(True, None, 2, 6, [], 7.0)),
    ]
    assert _synth(rows) == Synth(
        commits={1: [(0, 3, [2]), (1, RESUMED, [4])], 6: [(2, 7, [])]},
        exits={6: ()},
        left={},
    )


@pytest.mark.parametrize("end", [BACKSTOP_AT_5, NO_END], ids=["released", "no_end"])
def test_a_trailing_step_no_exit_vouches_for_and_nothing_resumed_from_is_left(end):
    assert _synth(_crashed_in_step_1(end)) == Synth(
        commits={1: [(0, 3, [2])]}, exits={}, left={4: Uncommitted.UNREACHED}
    )


def test_an_unattributed_stop_is_an_exit_node_that_vouches_for_nothing():
    """The rule for lone stops (Task 13) may leave a stop unattributed: a
    self-report that commits nothing (``values: null``), as an error exit does,
    so its claim's trailing step is not synthesized."""
    c = classify(parse(TRAILING))
    (end,) = c.ends.values()
    assert isinstance(end, _Exit)
    unattributed = Classification({1: _Unattributed(end.stop)}, c.later, c.claimless)
    assert _synth(TRAILING, unattributed) == Synth(
        commits={1: [(0, 3, [2]), (1, 5, [4])]},
        exits={1: None},
        left={6: Uncommitted.UNREACHED},
    )


def test_an_exit_that_reached_no_step_vouches_for_no_step():
    """A worker that exits before its first step writes ``final_step: null``:
    its exit reached no step, so every integer-step value of its claim is above
    it, a step-0 loading status included."""
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(LOADING_STATUS, 0, 1.5), name="status"),
        r(3, "lifecycle.stopped", _stopped(False, None, None, 1, [], 2.0)),
    ]
    assert _synth(rows) == Synth(
        commits={1: []}, exits={1: ()}, left={2: Uncommitted.ABOVE_THE_LAST_STEP}
    )


def test_a_retrys_loading_status_reaches_no_step():
    """A retry that crashed while loading wrote its status at step 0; C resumed
    from A's step 0. The retry did not reach step 0, so nothing is synthesized
    in it: A is the claim M1 will choose."""
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.stopped", _stopped(False, None, 0, 1, [], 3.0)),
        r(4, "lifecycle.started", _started("local://n1/12", 4.0)),
        r(5, "value", _value(LOADING_STATUS, 0, 4.5), name="status"),
        r(6, "lifecycle.stopped", _stopped(False, "OSError", None, 4, [], 5.0)),
        r(7, "lifecycle.started", _started("local://n1/13", 6.0)),
        r(8, "lifecycle.heartbeat", _beat(1, 0, 7, 7.0)),
        r(9, "lifecycle.stopped", _stopped(True, None, 1, 7, [], 8.0)),
    ]
    assert _synth(rows) == Synth(
        commits={1: [(0, 2, [])], 4: [], 7: [(1, 8, [])]},
        exits={1: (), 4: None, 7: ()},
        left={5: Uncommitted.ABOVE_THE_LAST_STEP},
    )


def _synthetics(rows):
    """``{(claim, old step): (place, t, consumed_seq)}`` of every synthetic
    commit."""
    return {
        (c.claim.started.seq, k.step): (
            k.origin.place,
            k.origin.t,
            k.origin.consumed_seq,
        )
        for c in _synthesis(rows).claims
        for k in c.commits
        if isinstance(k.origin, Synthetic)
    }


def test_m8_dates_a_synthetic_commit_by_its_last_dated_value_else_the_beat_before():
    """``t`` from the last value it commits that carries one, else from the real
    heartbeat before it; ``consumed_seq`` from that heartbeat."""
    assert _synthetics(SKIPPED) == {(1, 2): (SKIP, 4.0, 4), (1, 4): (SKIP, 5.0, 8)}
    assert _synthetics(TRAILING) == {(1, 2): (CLEAN, 4.0, 4)}
    assert _synthetics(RESUMED_PAST_A_LOADING_CRASH) == {(1, 2): (RESUMED, 4.0, 4)}


def test_m8_leaves_t_to_placement_when_there_is_none_to_carry():
    """A synthetic commit whose values carry no t, with no real heartbeat before
    it, takes the created_at of the record it is placed after (pass 5): t is
    None here. Its consumed_seq is 0: its claim witnessed no drain."""
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, None), name="loss"),
        r(3, "lifecycle.stopped", _stopped(True, None, 0, 1, [], 2.0)),
    ]
    assert _synthetics(rows) == {(1, 0): (CLEAN, None, 0)}


def test_the_claim_m1_will_choose_is_the_newest_before_it_to_reach_the_step():
    """In claim order, never by step nearness."""
    reached = [frozenset({0, 1, 2}), frozenset(), frozenset({2}), frozenset({5})]
    assert _newest_reaching(reached, 4, 2) == 2
    assert _newest_reaching(reached, 2, 2) == 0
    assert _newest_reaching(reached, 4, 3) is None
    assert _newest_reaching(reached, 3, 5) is None  # only the claims before it


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_every_value_is_committed_once_or_left_once(rid):
    rows = GOLDEN[rid]
    s = _synthesis(rows)
    assert [c.claim.started.seq for c in s.claims] == [
        c.seq for c in parse(rows).claims
    ]
    committed = [seq for c in s.claims for k in c.commits for seq in k.values]
    left = [x.value.seq for x in s.uncommitted]
    assert left == sorted(left)
    assert sorted(committed + left) == [x.seq for x in rows if x.topic == "value"]


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_p2_a_committed_value_is_its_claims_commit_at_its_step(rid):
    """P2: a committed value is committed by its claim's commit at its step, or
    is a leading value of its claim's first commit. A value no commit holds is
    stepless exactly when M3 leaves it."""
    s = _synthesis(GOLDEN[rid])
    for c in s.claims:
        own = {v.seq: v for v in c.claim.values}
        for i, k in enumerate(c.commits):
            assert k.values == tuple(sorted(k.values))
            for seq in k.values:
                step = own[seq].step
                assert step == k.step or (i == 0 and step is not None and step < k.step)
    for x in s.uncommitted:
        assert (x.why is Uncommitted.STEPLESS) == (x.value.step is None)


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_a_claims_commits_are_its_real_beats_and_m5s_three_places(rid):
    """Real commits are exactly the claim's integer-step heartbeats. Commits run
    in step order, gapless from the first real beat to the last. A skipped tick
    sits between them; a trailing step sits above the last and holds values."""
    for c in _synthesis(GOLDEN[rid]).claims:
        steps = [k.step for k in c.commits]
        assert steps == sorted(set(steps))
        real = [k.origin.beat for k in c.commits if isinstance(k.origin, Real)]
        assert real == [b for b in c.claim.beats if b.step is not None]
        assert all(
            k.step == k.origin.beat.step
            for k in c.commits
            if isinstance(k.origin, Real)
        )
        if real:
            assert set(range(real[0].step, real[-1].step + 1)) <= set(steps)
        for k in c.commits:
            if not isinstance(k.origin, Synthetic):
                continue
            if k.origin.place is SKIP:
                assert real and real[0].step < k.step < real[-1].step
            else:
                assert k.values and (not real or k.step > real[-1].step)


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_no_commit_holds_a_value_after_its_claims_end(rid):
    """M4, whatever ended the claim."""
    log = parse(GOLDEN[rid])
    ends = classify(log).ends
    for c in _synthesis(GOLDEN[rid]).claims:
        end = ends.get(c.claim.started.seq)
        if end is not None:
            assert all(seq < end.stop.seq for k in c.commits for seq in k.values)


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_a_claims_exit_node_is_its_self_reported_end(rid):
    """The worker's exit holds [] unless it errored, and null then; an
    unattributable stop holds null; a third party's release is no exit node."""
    ends = classify(parse(GOLDEN[rid])).ends
    for c in _synthesis(GOLDEN[rid]).claims:
        end = ends.get(c.claim.started.seq)
        if isinstance(end, _Exit):
            values = () if end.stop.error is None else None
            assert c.exit == ExitNode(end.stop, values)
        elif isinstance(end, _Unattributed):
            assert c.exit == ExitNode(end.stop, None)
        else:
            assert c.exit is None


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


@pytest.mark.parametrize(
    "first",
    [
        ("lifecycle.stopped", {"reason": "x"}),
        ("value", {"value": 1.0}),
        ("lifecycle.heartbeat", _beat(0, 0, 9, 1.0)),  # names no claim
    ],
)
def test_a_refusal_names_the_first_record_the_step_cannot_read(first):
    """In seq order, whatever the topics: a malformed claim after it does not
    pre-empt it."""
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


@pytest.mark.parametrize("steps", [[0, 0], [0, 2, 1]], ids=["repeat", "below"])
def test_an_in_claim_rewind_is_refused_by_seq(steps):
    """M9: two heartbeats of one step in one claim, or a step below an earlier
    one of the claim's: an in-claim rewind, which 0.3.0 did not record."""
    rows = [r(1, "lifecycle.started", _started("local://n1/11", 1.0))] + [
        r(2 + i, "lifecycle.heartbeat", _beat(s, 0, 1, 2.0 + i))
        for i, s in enumerate(steps)
    ]
    match = rf"heartbeat at seq {len(steps) + 1} .*in-claim rewind"
    with pytest.raises(MigrationError, match=match):
        parse(rows)
    with pytest.raises(MigrationError, match=match):
        step.transform(rows)


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


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_transform_runs_the_passes_it_has_and_writes_nothing_yet(rid):
    """Passes 1-3 run, and their refusals stand; pass 4 is the first missing."""
    with pytest.raises(NotImplementedError, match=r"writes nothing yet: pass 4 "):
        step.transform(GOLDEN[rid])


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
