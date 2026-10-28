"""The 0.3.0 -> 0.4.0 step's golden logs: data only (the plan's Task 22).

The logs are 0.3.0-shaped by hand, as a 0.3.0 worker and its third parties wrote
them: heartbeats and stoppeds that name their claim, values that name none. The
builders make 0.3.0 bodies (lifecycle-v0.5, value-v0.2). The step's tests restate
each as (log, facts, choices) -> output, and the simulator's tests render each from a
history: so this module imports neither the step nor the simulator.

Each log's comment says what the log is. A comment that cites a rule M1-M10, or
"pass 1", names the rule the step's passes applied before the step was rebuilt on its
candidate engine (Task 22): the logs were written to exercise those rules, and what
the step now makes of each, under its history's facts, is pinned in the harness.
"""

import json
import socket

from runstate.migrations import Row


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


def _launched(handle, t):
    return {"handle": handle, "status": "running", "t": t}


def _terminated(reason, exit_code, signal, t):
    return {"reason": reason, "exit_code": exit_code, "signal": signal, "t": t}


def _dated(rows):
    """Every record its own created_at, so a test can see each one kept; a
    well-formed stop's is its own ``t``, as a self-report's is."""
    assert [x.seq for x in rows] == list(range(1, len(rows) + 1))
    return [x._replace(created_at=_written_at(x)) for x in rows]


def _written_at(x):
    # a stop is written when it reports, as a self-report is (the rule for lone stops)
    if x.topic == "lifecycle.stopped":
        body = json.loads(x.body)
        if "t" in body:
            return body["t"]
    return 1000.0 + x.seq / 4


def _written_later(rows, seq, by):
    """``rows``, with the stop at ``seq`` written ``by`` seconds after the ``t``
    it reports."""
    (x,) = [y for y in rows if y.seq == seq]
    later = x._replace(created_at=json.loads(x.body)["t"] + by)
    return [later if y.seq == seq else y for y in rows]


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

# The other classes of third-party stop (review §12, item 14), one log each.
#
# A later stop with its error set: the worker exited, then a third party
# reported a cause. It becomes a death.
LATER_ERROR = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.stopped", _stopped(False, None, 0, 1, [], 3.0)),
        r(
            4,
            "lifecycle.stopped",
            _stopped(False, "slurm: NODE_FAIL", None, 1, [], 4.0),
        ),
    ]
)

# A default-body stopped, then a completed one, both naming the claim. Under its
# history the first is a third party's note, which leaves the log, and the
# completion is the claim's exit, which honours the stop.
LATER_DEFAULT = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "control.stop", {}, rid="halt"),
        r(4, "lifecycle.stopped", _stopped(False, None, 0, 1, [], 3.0)),
        r(5, "lifecycle.stopped", _stopped(True, None, 0, 1, ["halt"], 4.0)),
    ]
)

# M10's body naming a claim that had already ended: a later stop with no error,
# so only its withdrawal, dated by its created_at, since it has no t.
LATER_RELEASE = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "control.stop", {}, rid="stop@3"),
        r(4, "lifecycle.stopped", _stopped(False, None, 0, 1, [], 3.0)),
        r(5, "lifecycle.stopped", _release("reclaimed: slurm TIMEOUT", ["stop@3"], 1)),
    ]
)

# A later stop with its error set that honored a stop: the death in its place,
# then the withdrawal after it.
LATER_ERROR_HONORED = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "control.stop", {}, rid="halt"),
        r(4, "lifecycle.stopped", _stopped(False, None, 0, 1, [], 3.0)),
        r(
            5,
            "lifecycle.stopped",
            _stopped(False, "slurm: NODE_FAIL", None, 1, ["halt"], 4.0),
        ),
    ]
)

# A claimless stop with its error set, not the backstop: a death names a claim,
# so only its withdrawal.
CLAIMLESS_ERROR = _dated(
    [
        r(1, "control.stop", {}, rid="pre"),
        r(2, "lifecycle.stopped", _stopped(False, "boom", None, None, ["pre"], 1.0)),
    ]
)

# M10's body naming no claim: withdrawals only.
CLAIMLESS_RELEASE = _dated(
    [
        r(1, "control.stop", {}, rid="pre"),
        r(2, "lifecycle.stopped", _release("reclaimed: never ran", ["pre"], None)),
    ]
)

# The backstop naming no claim (owner ruling c'): a supervisor.terminated in its
# place, under a minted launch id, killed by the signal its text names; the
# stop it honored is withdrawn after it.
CLAIMLESS_BACKSTOP = _dated(
    [
        r(1, "control.stop", {}, rid="pre"),
        r(
            2,
            "lifecycle.stopped",
            _stopped(
                False,
                "subprocess exited -9 with no terminal record",
                None,
                None,
                ["pre"],
                1.0,
            ),
        ),
        r(3, "lifecycle.started", _started("local://n1/11", 2.0)),
    ]
)

# A second backstop naming a claim the first already released (owner ruling
# c'): the first is the claim's death, the second a terminated, so the claim
# has one death.
SECOND_BACKSTOP = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.stopped", _stopped(False, BACKSTOP, 0, 1, [], 3.0)),
        r(
            4,
            "lifecycle.stopped",
            _stopped(
                False, "subprocess exited 137 with no terminal record", 0, 1, [], 4.0
            ),
        ),
    ]
)

# The minted launch id is fresh against every request id in the run.
BACKSTOP_ID_TAKEN = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "control.stop", {}, rid="backstop@5"),
        r(4, "lifecycle.stopped", _stopped(False, None, 0, 1, ["backstop@5"], 3.0)),
        r(5, "lifecycle.stopped", _stopped(False, BACKSTOP, 0, 1, [], 4.0)),
    ]
)

# A launched run whose worker exited: launcher.* records move to supervisor.*,
# their bodies unchanged.
LAUNCHED = _dated(
    [
        r(
            1,
            "launcher.launched",
            {"handle": "local://n1/11", "status": "running", "t": 0.5},
            rid="L1",
        ),
        r(2, "lifecycle.started", _started("local://n1/11", 1.0), rid="L1"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 2, 2.0)),
        r(4, "lifecycle.stopped", _stopped(True, None, 0, 2, [], 3.0)),
        r(
            5,
            "launcher.terminated",
            {"reason": "exited", "exit_code": 0, "signal": None, "t": 4.0},
            rid="L1",
        ),
    ]
)

# Attributed death reports (§7): a claim that 0.3.0 saw ended only by its
# launch's terminated.
#
# The launch's handle is the claim's, compared whole: a died from the
# terminated, placed just after it, killed by the signal it names.
ATTRIBUTED = _dated(
    [
        r(1, "launcher.launched", _launched("local://n1/11", 0.5), rid="L1"),
        r(2, "lifecycle.started", _started("local://n1/11", 1.0), rid="L1"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 2, 2.0)),
        r(4, "launcher.terminated", _terminated("killed", None, 9, 4.0), rid="L1"),
    ]
)

# A wrapper's launch (an sbatch, a shell): its handle is not the claim's, so its
# exit says nothing of the worker, and no died is written.
WRAPPER = _dated(
    [
        r(1, "launcher.launched", _launched("local://n1/100", 0.5), rid="L1"),
        r(2, "lifecycle.started", _started("local://n1/101", 1.0), rid="L1"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 2, 2.0)),
        r(4, "launcher.terminated", _terminated("exited", 0, None, 4.0), rid="L1"),
    ]
)

# The claim's pid on another host is another process: compared whole, no died.
ANOTHER_HOST = _dated(
    [
        r(1, "launcher.launched", _launched("local://n2/11", 0.5), rid="L1"),
        r(2, "lifecycle.started", _started("local://n1/11", 1.0), rid="L1"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 2, 2.0)),
        r(4, "launcher.terminated", _terminated("exited", 1, None, 4.0), rid="L1"),
    ]
)

# A late reap after a relaunch. A's child claims before its parent writes
# launched, which is found by its launch id wherever it is. A crashes; B is
# launched, resumes from A's step 0 and exits; A's reap lands after B's exit. The
# died names A, just after A's terminated, and errored, by its nonzero exit. B's
# own exit ended B, so B's terminated attributes nothing.
LATE_REAP = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0), rid="L1"),
        r(2, "launcher.launched", _launched("local://n1/11", 1.5), rid="L1"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "launcher.launched", _launched("local://n1/12", 3.0), rid="L2"),
        r(5, "lifecycle.started", _started("local://n1/12", 4.0), rid="L2"),
        r(6, "lifecycle.heartbeat", _beat(1, 0, 5, 5.0)),
        r(7, "lifecycle.stopped", _stopped(True, None, 1, 5, [], 6.0)),
        r(8, "launcher.terminated", _terminated("exited", 1, None, 7.0), rid="L1"),
        r(9, "launcher.terminated", _terminated("exited", 0, None, 8.0), rid="L2"),
    ]
)

# One launch claimed twice (a target that ran two Workers), and nothing ended
# either claim: the later claim's gate proves the first dead, a lost death report
# just before its started, and the launch's terminated reports the newest claim
# naming it dead, lost by the exit 0, as §3's live launcher reports only for it.
TWO_CLAIMS_ONE_LAUNCH = _dated(
    [
        r(1, "launcher.launched", _launched("local://n1/11", 0.5), rid="L1"),
        r(2, "lifecycle.started", _started("local://n1/11", 1.0), rid="L1"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 2, 2.0)),
        r(4, "lifecycle.started", _started("local://n1/11", 3.0), rid="L1"),
        r(5, "lifecycle.heartbeat", _beat(1, 0, 4, 4.0)),
        r(6, "launcher.terminated", _terminated("exited", 0, None, 5.0), rid="L1"),
    ]
)

# A value after an attributed death (M4): the terminated a died is attributed
# from ends its claim, as a stopped would. A beat 0 and wrote step 1's value, and
# its launch was killed; a step-1 value lands after the reap, which pass 1 gives
# A by position. B resumed from step 1: its first real beat is 2. A's step 1 is
# synthesized for B (M5's third place) with the value before the reap only; the
# one after it stays uncommitted, after its claim's end.
AFTER_AN_ATTRIBUTED_DEATH = _dated(
    [
        r(1, "launcher.launched", _launched("local://n1/11", 0.5), rid="L1"),
        r(2, "lifecycle.started", _started("local://n1/11", 1.0), rid="L1"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 2, 2.0)),
        r(4, "value", _value(0.5, 1, 3.0), name="loss"),
        r(5, "launcher.terminated", _terminated("killed", None, 9, 4.0), rid="L1"),
        r(6, "value", _value(0.4, 1, 5.0), name="loss"),
        r(7, "lifecycle.started", _started("local://n1/12", 6.0)),
        r(8, "lifecycle.heartbeat", _beat(2, 0, 7, 7.0)),
    ]
)

# The rule for lone stops (§7): a well-formed lone stop that is not the backstop
# is the worker's exit unless the log contradicts that. One log per
# contradiction, the boundary of the second, and the cooperative stop the census
# found to be the worker's own.
#
# The reclaim tool (mycooc's, ffe05f6e..15e43041): A beat steps 0 and 1 and
# wrote step 2's value; its job ended, and the tool wrote a default body dated
# with SLURM's end time, 1,500 s before its record. Written after the time it
# reports, it is a third party's release: a death, preempted, with the
# migration's label. No exit vouches for step 2, so its value stays an
# unreached trailing step. Its 10-23 version writes the cause: errored.
SACCT = (
    "SLURM job 123 (0) ended TIMEOUT (exit 0:0) while its claim was held "
    "(reported from sacct by scripts/reclaim_experiment.py)"
)


def _reclaimed(error):
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(4, "value", _value(0.7, 2, 4.0), name="loss"),
        r(5, "lifecycle.stopped", _stopped(False, error, None, 1, [], 5.0)),
    ]
    return _written_later(_dated(rows), 5, 1500.0)


RECLAIM_REPORTED_LATER = _reclaimed(None)
RECLAIM_OF_10_23 = _reclaimed(SACCT)

# A stop its claim beats after: the worker outlived it, so it is a third party's.
# Under its history it is a note, which ends nothing and leaves the log, and the
# beat after it commits.
BEATEN_AFTER = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.stopped", _stopped(False, None, 0, 1, [], 3.0)),
        r(4, "lifecycle.heartbeat", _beat(1, 0, 1, 4.0)),  # still running
    ]
)

# The boundary: A's stop is written 59 s after the time it reports, the worker's
# exit; B's, the same body, 61 s after, a third party's release.
REPORTED_LATER_BOUNDARY = _written_later(
    _written_later(
        _dated(
            [
                r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
                r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
                r(3, "lifecycle.stopped", _stopped(False, None, 0, 1, [], 3.0)),
                r(4, "lifecycle.started", _started("local://n1/12", 4.0)),
                r(5, "lifecycle.heartbeat", _beat(1, 0, 4, 5.0)),
                r(6, "lifecycle.stopped", _stopped(False, None, 1, 4, [], 6.0)),
            ]
        ),
        3,
        59.0,
    ),
    6,
    61.0,
)

# The cooperative stop, the census's collision shape (51 genuine in mycooc): the
# worker drained the runner's stop, beat with a watermark covering it, wrote its
# completion reason, then exited with a default body and final_step set,
# honoring the stop, written when it reports, and wrote its status. The
# worker's exit.
COOPERATIVE = _written_later(
    _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "value", _value(0.9, 0, 2.0), name="loss"),
            r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
            r(4, "control.stop", {}, rid="timeout"),
            r(5, "value", _value(0.8, 1, 3.0), name="loss"),
            r(6, "lifecycle.heartbeat", _beat(1, 4, 1, 3.0)),
            r(7, "value", _value("preempted", None, 3.5), name="completion_reason"),
            r(8, "lifecycle.stopped", _stopped(False, None, 1, 1, ["timeout"], 4.0)),
            r(9, "value", _value("done", None, 4.5), name="status"),
        ]
    ),
    8,
    0.0,
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
# crashed in step 2 after writing its values. B, a retry, wrote mycooc's step-0
# loading status, crashed while loading and beat nothing. C resumed from step
# 2's checkpoint: its first real beat is 3. A is the newest claim before C to
# reach step 2, by trailing values, so A's step 2 is synthesized, though A's
# exit is an error. B beat nothing, so it reached no step: its status stays
# uncommitted as loading.
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
        r(9, "value", _value(LOADING_STATUS, 0, 6.5), name="status"),
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


def _loading_retry(end):
    """A beat step 0 and errored. B, a retry, wrote mycooc's step-0 loading
    status and ended as ``end`` says, with no exit node. C resumed from A's step
    0: its first real beat is 1."""
    return _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "value", _value(0.9, 0, 2.0), name="loss"),
            r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
            r(4, "lifecycle.stopped", _stopped(False, "boom", 0, 1, [], 3.0)),
            r(5, "lifecycle.started", _started("local://n1/12", 4.0)),
            r(6, "value", _value(LOADING_STATUS, 0, 4.5), name="status"),
            r(7, *end),
            r(8, "lifecycle.started", _started("local://n1/13", 6.0)),
            r(9, "value", _value(0.8, 1, 7.0), name="loss"),
            r(10, "lifecycle.heartbeat", _beat(1, 0, 8, 7.0)),
            r(11, "lifecycle.stopped", _stopped(True, None, 1, 8, [], 8.0)),
        ]
    )


# A loading retry that nothing vouches for still reaches no step (review of
# 6ea3fd8): B has no exit node, so no last step caps it, but B beat nothing, so
# its step-0 status is a loading value. Nothing is synthesized in B, and C will
# parent on A's step 0. Once with the runner's backstop releasing B, once with
# nothing.
LOADING_RETRY_RELEASED = _loading_retry(
    ("lifecycle.stopped", _stopped(False, BACKSTOP, None, 5, [], 5.0))
)
LOADING_RETRY_UNENDED = _loading_retry(("user.note", {"killed": True}))

# The retry's loading status at its resume step, 2, not 0 (review of 32c6f97).
# A crashed in step 2 after writing its values; B, released by the backstop,
# wrote its status at step 2 while it loaded; C's first real beat is 3. B beat
# nothing, so it reaches no step, and A's step 2 is synthesized. Inferring B a
# position from A gave two self-consistent answers here: at A's step 1 the
# status trailed and B took the synthesis, at A's step 2 it was loading and A
# took it.
LOADING_RETRY_AT_ITS_RESUME_STEP = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "value", _value(0.8, 1, 3.0), name="loss"),
        r(5, "lifecycle.heartbeat", _beat(1, 4, 1, 3.0)),
        r(6, "value", _value(0.7, 2, 4.0), name="loss"),
        r(7, "lifecycle.stopped", _stopped(False, "CUDA out of memory", 2, 1, [], 5.0)),
        r(8, "lifecycle.started", _started("local://n1/12", 6.0)),
        r(9, "value", _value(LOADING_STATUS, 2, 6.5), name="status"),
        r(10, "lifecycle.stopped", _stopped(False, BACKSTOP, None, 8, [], 7.0)),
        r(11, "lifecycle.started", _started("local://n1/13", 8.0)),
        r(12, "value", _value(0.6, 3, 9.0), name="loss"),
        r(13, "lifecycle.heartbeat", _beat(3, 0, 11, 9.0)),
        r(14, "lifecycle.stopped", _stopped(True, None, 3, 11, [], 10.0)),
    ]
)

# The cost of that reading (a known limit): B resumed from A's step 1, wrote
# all of step 2's values and its checkpoint, and the backstop released it
# before its first tick; C resumed from step 2: its first real beat is 3. B
# beat nothing, so nothing is synthesized in B, and no claim holds step 2:
# C's first commit will be a parentless resume, which P3 names.
BEATLESS_FULL_STEP = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(4, "lifecycle.stopped", _stopped(False, None, 1, 1, [], 4.0)),
        r(5, "lifecycle.started", _started("local://n1/12", 5.0)),
        r(6, "value", _value(0.7, 2, 6.0), name="loss"),
        r(7, "value", _value({"status": "aligning step 2"}, 2, 6.5), name="status"),
        r(8, "lifecycle.stopped", _stopped(False, BACKSTOP, None, 5, [], 7.0)),
        r(9, "lifecycle.started", _started("local://n1/13", 8.0)),
        r(10, "value", _value(0.6, 3, 9.0), name="loss"),
        r(11, "lifecycle.heartbeat", _beat(3, 0, 9, 9.0)),
        r(12, "lifecycle.stopped", _stopped(True, None, 3, 9, [], 10.0)),
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

# The displacement-parent history (Review Focus 2). A beats 0 and 1 and stalls.
# B claims, resumes from A's step 1 and beats 2; A, displaced but still
# running, writes step 2's value and beat after B's, and both exit. C resumes
# from step 2: its first real beat is 3. Two claims hold a commit at step 2,
# and A's beat is the later record. M1 takes B's, the newest claim to hold one;
# the draft's "latest earlier heartbeat one step behind" took A's, a displaced
# worker's branch, which turns a splice into a permanent name.
DISPLACEMENT_PARENT = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "value", _value(0.8, 1, 3.0), name="loss"),
        r(5, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(6, "lifecycle.started", _started("local://n2/22", 5.0)),
        r(7, "value", _value(0.7, 2, 6.0), name="loss"),
        r(8, "lifecycle.heartbeat", _beat(2, 0, 6, 6.0)),
        r(9, "value", _value(0.71, 2, 6.5), name="loss"),  # A's
        r(10, "lifecycle.heartbeat", _beat(2, 0, 1, 6.5)),  # A's
        r(11, "lifecycle.stopped", _stopped(False, None, 2, 6, [], 7.0)),
        r(12, "lifecycle.stopped", _stopped(False, None, 2, 1, [], 7.5)),  # A's
        r(13, "lifecycle.started", _started("local://n3/33", 8.0)),
        r(14, "value", _value(0.6, 3, 9.0), name="loss"),
        r(15, "lifecycle.heartbeat", _beat(3, 0, 13, 9.0)),
        r(16, "lifecycle.stopped", _stopped(True, None, 3, 13, [], 10.0)),
    ]
)

# A commitless claim: B beat only without a step, after only stepless status
# values (mycooc's stepless-only claims), and exited. It holds no commit, so its
# resume point is inferred: the newest commit of the newest claim before it to
# hold one, A's step 1. B's exit's parent is that resume point. C, resuming from
# step 1, passes over B to A's step 1.
COMMITLESS = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(4, "lifecycle.stopped", _stopped(False, None, 1, 1, [], 4.0)),
        r(5, "lifecycle.started", _started("local://n1/12", 5.0)),
        r(6, "value", _value({"status": "loading"}, None, 5.5), name="status"),
        r(7, "lifecycle.heartbeat", _beat(None, 0, 5, 6.0)),
        r(8, "value", _value({"status": "waiting"}, None, 6.5), name="status"),
        r(9, "lifecycle.heartbeat", _beat(None, 0, 5, 7.0)),
        r(10, "lifecycle.stopped", _stopped(False, None, None, 5, [], 8.0)),
        r(11, "lifecycle.started", _started("local://n1/13", 9.0)),
        r(12, "lifecycle.heartbeat", _beat(2, 0, 11, 10.0)),
        r(13, "lifecycle.stopped", _stopped(True, None, 2, 11, [], 11.0)),
    ]
)

# A parentless resume: A beat 0 and 1, finished step 2 and saved its checkpoint,
# but wrote no step-2 values and crashed before its beat; the runner's backstop
# released it. B resumed from that checkpoint: its first real beat is 3. Nothing
# says A reached step 2, so no claim holds it (M5 synthesizes only where values
# say a step was reached). M1 does not fall back to A's step 1, the nearest step
# below: B's first commit has no parent, a parentless resume, named so.
RESUMED_PAST_AN_UNRECORDED_STEP = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "value", _value(0.8, 1, 3.0), name="loss"),
        r(5, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(6, "lifecycle.stopped", _stopped(False, BACKSTOP, 1, 1, [], 4.0)),
        r(7, "lifecycle.started", _started("local://n1/12", 5.0)),
        r(8, "value", _value(0.6, 3, 6.0), name="loss"),
        r(9, "lifecycle.heartbeat", _beat(3, 0, 7, 6.0)),
        r(10, "value", _value(0.5, 4, 7.0), name="loss"),
        r(11, "lifecycle.heartbeat", _beat(4, 0, 7, 7.0)),
        r(12, "lifecycle.stopped", _stopped(True, None, 4, 7, [], 8.0)),
    ]
)

# A resume past a preempted exit, which parents on the commit (review §12, item
# 14). A beat 0 and 1 and honored a stop: its exit did not discard, so it is
# extendable, and it follows A's step-1 commit. B resumed from step 1's
# checkpoint: its first real beat is 2. M1 parents B on A's step-1 commit, not on
# A's exit, since every 0.3.0 checkpoint was saved at a step: A's exit and B's
# first commit are siblings, both children of A's step 1.
RESUMED_PAST_A_PREEMPTED_EXIT = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "value", _value(0.8, 1, 3.0), name="loss"),
        r(5, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(6, "control.stop", {}, rid="preempt"),
        r(7, "lifecycle.stopped", _stopped(False, None, 1, 1, ["preempt"], 4.0)),
        r(8, "lifecycle.started", _started("local://n1/12", 5.0)),
        r(9, "value", _value(0.7, 2, 6.0), name="loss"),
        r(10, "lifecycle.heartbeat", _beat(2, 0, 8, 6.0)),
        r(11, "lifecycle.stopped", _stopped(True, None, 2, 8, [], 7.0)),
    ]
)

# A loading retry beside a displaced worker: A beat 0 and 1; B claimed and
# crashed while loading; A, displaced, beat 2 after B's claim, and after B's
# exit. M1's candidates for B are the commits placed below B's started, so A's
# step 2 is none: B's inferred resume point, and its exit's parent, is A's
# step 1.
LOADING_RETRY_BESIDE_A_DISPLACED_WORKER = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(4, "lifecycle.started", _started("local://n2/22", 4.0)),
        r(5, "lifecycle.stopped", _stopped(False, "OSError", None, 4, [], 5.0)),
        r(6, "lifecycle.heartbeat", _beat(2, 0, 1, 6.0)),  # A's
    ]
)

# A resume from a step a displaced worker committed after the claim (a known
# limit): A beat 0 and 1; B claimed; A, displaced, saved step 2's checkpoint and
# beat 2 after B's claim, and B, loading it, first beat 3. A's step 2 is placed
# above B's started, so it is no candidate, and no other claim holds step 2:
# B's first commit is a parentless resume, which P3 names.
RESUMED_FROM_A_STEP_COMMITTED_AFTER_THE_CLAIM = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(4, "lifecycle.started", _started("local://n2/22", 4.0)),
        r(5, "lifecycle.heartbeat", _beat(2, 0, 1, 5.0)),  # A's
        r(6, "lifecycle.heartbeat", _beat(3, 0, 4, 6.0)),
        r(7, "lifecycle.stopped", _stopped(True, None, 3, 4, [], 7.0)),
    ]
)

# Doubly displaced: A beat 0, 1 and 2. B branched from A's step 0; A, displaced,
# wrote a step-3 value after B's claim, which pass 1 gives B. B beat 1, and,
# displaced by C in turn, beat 2 after C's claim. C first beat 4. Pass 3's third
# place synthesizes B's trailing step 3 for C, since B is the newest claim to
# reach it; but B's step 3 is placed after its parent, B's step 2, beaten above
# C's started, so pass 4 passes over it. No other claim holds step 3: C's first
# commit is a parentless resume, and nothing in this log continues B's step 3.
DOUBLY_DISPLACED = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(4, "lifecycle.heartbeat", _beat(2, 0, 1, 4.0)),
        r(5, "lifecycle.started", _started("local://n2/22", 5.0)),
        r(6, "value", _value(0.5, 3, 6.0), name="loss"),  # A's
        r(7, "lifecycle.heartbeat", _beat(1, 0, 5, 7.0)),
        r(8, "lifecycle.started", _started("local://n3/33", 8.0)),
        r(9, "lifecycle.heartbeat", _beat(2, 0, 5, 9.0)),  # B's
        r(10, "lifecycle.heartbeat", _beat(4, 0, 8, 10.0)),
    ]
)

# A resume from a tick a displaced worker skipped after the claim: A beat 0; B
# claimed; A, displaced, beat 1 and 3 after B's claim, skipping step 2's tick,
# and B, loading A's step-2 checkpoint, first beat 3. A's step 2 is synthesized
# (a skipped tick), and is placed after its parent, A's step 1, beaten above B's
# started: it is no candidate, as A's real step 2 would not be, and B's first
# commit is a parentless resume.
RESUMED_FROM_A_TICK_SKIPPED_AFTER_THE_CLAIM = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.started", _started("local://n2/22", 3.0)),
        r(4, "lifecycle.heartbeat", _beat(1, 0, 1, 4.0)),  # A's
        r(5, "lifecycle.heartbeat", _beat(3, 0, 1, 5.0)),  # A's
        r(6, "lifecycle.heartbeat", _beat(3, 0, 3, 6.0)),
        r(7, "lifecycle.stopped", _stopped(True, None, 3, 3, [], 7.0)),
    ]
)

# A resume from a tick skipped across the claim: A beat 0 and 1; B claimed; A,
# displaced, beat 3 after B's claim, skipping step 2's tick, and B, loading A's
# step-2 checkpoint, first beat 3. A's step 2 is synthesized (a skipped tick),
# and is placed after its parent, A's step 1, beaten below B's started: it is a
# candidate, though the real beat after it follows B's started, and B resumes
# from it.
RESUMED_FROM_A_TICK_SKIPPED_ACROSS_THE_CLAIM = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(4, "lifecycle.started", _started("local://n2/22", 4.0)),
        r(5, "lifecycle.heartbeat", _beat(3, 0, 1, 5.0)),  # A's
        r(6, "lifecycle.heartbeat", _beat(3, 0, 4, 6.0)),
        r(7, "lifecycle.stopped", _stopped(True, None, 3, 4, [], 7.0)),
    ]
)

# An older claim's candidate below a newer claim's later step: A beat 0, 1 and 2
# and was preempted. B branched from A's step 0, beat 1, and, displaced by C,
# beat 2 after C's claim. C first beat 3. B is the newest claim to hold step 2,
# but its step 2 is placed above C's started, so M1 takes A's, the newest
# candidate.
RESUMED_ON_AN_OLDER_CLAIMS_STEP = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(4, "lifecycle.heartbeat", _beat(2, 0, 1, 4.0)),
        r(5, "lifecycle.stopped", _stopped(False, None, 2, 1, [], 5.0)),
        r(6, "lifecycle.started", _started("local://n2/22", 6.0)),
        r(7, "lifecycle.heartbeat", _beat(1, 0, 6, 7.0)),
        r(8, "lifecycle.started", _started("local://n3/33", 8.0)),
        r(9, "lifecycle.heartbeat", _beat(2, 0, 6, 9.0)),  # B's
        r(10, "lifecycle.heartbeat", _beat(3, 0, 8, 10.0)),
        r(11, "lifecycle.stopped", _stopped(True, None, 3, 8, [], 11.0)),
    ]
)

# Watermarks that renumbering moves: step 1's tick is skipped, so a commit is
# synthesized before the stop at seq 4, which moves to seq 5. The stepless beat
# and step 3's commit drained it; each watermark is renamed to seq 5, and the
# exit honors the stop by its id.
WATERMARKED = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(2, 0, 1, 3.0)),
        r(4, "control.stop", {}, rid="halt"),
        r(5, "lifecycle.heartbeat", _beat(None, 4, 1, 4.0)),
        r(6, "lifecycle.heartbeat", _beat(3, 4, 1, 5.0)),
        r(7, "lifecycle.stopped", _stopped(False, None, 3, 1, ["halt"], 6.0)),
    ]
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
    "loading_retry_released": LOADING_RETRY_RELEASED,
    "loading_retry_unended": LOADING_RETRY_UNENDED,
    "loading_retry_at_its_resume_step": LOADING_RETRY_AT_ITS_RESUME_STEP,
    "beatless_full_step": BEATLESS_FULL_STEP,
    "displacement_parent": DISPLACEMENT_PARENT,
    "commitless": COMMITLESS,
    "resumed_past_an_unrecorded_step": RESUMED_PAST_AN_UNRECORDED_STEP,
    "resumed_past_a_preempted_exit": RESUMED_PAST_A_PREEMPTED_EXIT,
    "loading_retry_beside_a_displaced_worker": LOADING_RETRY_BESIDE_A_DISPLACED_WORKER,
    "resumed_from_a_step_committed_after_the_claim": (
        RESUMED_FROM_A_STEP_COMMITTED_AFTER_THE_CLAIM
    ),
    "resumed_on_an_older_claims_step": RESUMED_ON_AN_OLDER_CLAIMS_STEP,
    "doubly_displaced": DOUBLY_DISPLACED,
    "resumed_from_a_tick_skipped_after_the_claim": (
        RESUMED_FROM_A_TICK_SKIPPED_AFTER_THE_CLAIM
    ),
    "resumed_from_a_tick_skipped_across_the_claim": (
        RESUMED_FROM_A_TICK_SKIPPED_ACROSS_THE_CLAIM
    ),
    "watermarked": WATERMARKED,
    "later_error": LATER_ERROR,
    "later_default": LATER_DEFAULT,
    "later_release": LATER_RELEASE,
    "later_error_honored": LATER_ERROR_HONORED,
    "claimless_error": CLAIMLESS_ERROR,
    "claimless_release": CLAIMLESS_RELEASE,
    "claimless_backstop": CLAIMLESS_BACKSTOP,
    "second_backstop": SECOND_BACKSTOP,
    "backstop_id_taken": BACKSTOP_ID_TAKEN,
    "launched": LAUNCHED,
    "attributed": ATTRIBUTED,
    "wrapper": WRAPPER,
    "another_host": ANOTHER_HOST,
    "late_reap": LATE_REAP,
    "two_claims_one_launch": TWO_CLAIMS_ONE_LAUNCH,
    "after_an_attributed_death": AFTER_AN_ATTRIBUTED_DEATH,
    "reclaim_reported_later": RECLAIM_REPORTED_LATER,
    "reclaim_of_10_23": RECLAIM_OF_10_23,
    "beaten_after": BEATEN_AFTER,
    "reported_later_boundary": REPORTED_LATER_BOUNDARY,
    "cooperative": COOPERATIVE,
}


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
