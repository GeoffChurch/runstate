"""The 0.3.0 -> 0.4.0 step (commits-and-lineage.md §7; log-formats.md §6).

The golden logs are 0.3.0-shaped by hand, as a 0.3.0 worker and its third
parties wrote them: heartbeats and stoppeds that name their claim, values that
name none. The builders make 0.3.0 bodies (lifecycle-v0.5, value-v0.2); the
0.2.0 step's test module makes 0.2.0 ones.

Each pass is tested on its result: a pass that reads only the parsed log on
that, every later one as ``run_passes``, the step's one wiring of the passes,
gives it, so no test wires the passes apart. Every golden log's end-to-end
output (seq, topic, name, request_id and body of every row) is asserted exactly,
in ``OUTPUT``, derived by hand from the golden's comment and §7; a golden with
no entry there fails. Every row is also validated against the 0.4.0 schemas."""

import functools
import itertools
import json
import os
import re
import socket
from pathlib import Path
from typing import NamedTuple

import pytest

from runstate import migrations
from runstate.channel.sqlite import SqliteChannel
from runstate.formats import FORMATS, DirectoryLayout
from runstate.migrations import RENUMBERING_FROM, STEPS, MigrationError, Row, migrate
from runstate.migrations.stores import SqliteStore
from runstate.migrations.v0_3_0_to_v0_4_0 import (
    After,
    Attributed,
    CommitRef,
    ExitNode,
    Fresh,
    Inferred,
    Input,
    NoSeqs,
    Place,
    Parentless,
    Real,
    Resumed,
    ResumeFrom,
    Synthesized,
    Synthetic,
    ToCommit,
    ToDied,
    ToExit,
    ToHeartbeat,
    ToReap,
    ToStarted,
    ToValue,
    ToWithdraw,
    Uncommitted,
    V0_3_0_to_V0_4_0,
    Withdrawal,
    _Exit,
    _Malformed,
    _newest_reaching,
    _Release,
    _Stopped,
    _Unattributed,
    attribute,
    classify,
    parse,
    renumber,
    run_passes,
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


def _launched(handle, t):
    return {"handle": handle, "status": "running", "t": t}


def _terminated(reason, exit_code, signal, t):
    return {"reason": reason, "exit_code": exit_code, "signal": signal, "t": t}


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

# A later stop with no error, a "clear staged stop" default body, completed
# even: it becomes only the withdrawal of the stop it honored, never a death and
# never a completion.
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
# either claim: only the newest claim naming the launch gets a died, as §3's
# live launcher reports only for it, lost by the exit 0. The older claim stays
# as 0.3.0 left it.
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
        r(8, "lifecycle.stopped", _stopped(False, BACKSTOP, 1, 5, [], 7.0)),
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
    """It ends no claim: a died must name one (supervisor-v0.5's required
    integer claim_seq). It becomes a terminated (owner ruling c')."""
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


def _synthesis(rows):
    return run_passes(rows).synthesis


def _origin(commit):
    if isinstance(commit.origin, Real):
        return commit.origin.beat.seq
    return commit.origin.place


def _synth(rows):
    s = _synthesis(rows)
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
        left={9: Uncommitted.LOADING},
    )


@pytest.mark.parametrize("rid", ["loading_retry_released", "loading_retry_unended"])
def test_a_loading_retry_nothing_vouches_for_reaches_no_step(rid):
    """B beat nothing, so it reaches no step: nothing is synthesized in B, and
    its step-0 status stays uncommitted as loading."""
    assert _synth(GOLDEN[rid]) == Synth(
        commits={1: [(0, 3, [2])], 5: [], 8: [(1, 10, [9])]},
        exits={1: None, 8: ()},
        left={6: Uncommitted.LOADING},
    )


def test_a_loading_retry_at_its_resume_step_reaches_no_step():
    """One answer, not two: B's status at step 2 is a loading value, so A, the
    newest claim before C to reach step 2, takes the synthesis."""
    assert _synth(LOADING_RETRY_AT_ITS_RESUME_STEP) == Synth(
        commits={
            1: [(0, 3, [2]), (1, 5, [4]), (2, RESUMED, [6])],
            8: [],
            11: [(3, 13, [12])],
        },
        exits={1: None, 11: ()},
        left={9: Uncommitted.LOADING},
    )


def test_a_beatless_claim_that_finished_a_step_reaches_no_step():
    """The reading's cost: B's step-2 values stay loading values, and nothing is
    synthesized, though C resumed from step 2."""
    assert _synth(BEATLESS_FULL_STEP) == Synth(
        commits={1: [(0, 2, []), (1, 3, [])], 5: [], 9: [(3, 11, [10])]},
        exits={1: (), 9: ()},
        left={6: Uncommitted.LOADING, 7: Uncommitted.LOADING},
    )


def test_a_beatless_claim_commits_nothing_even_before_a_clean_exit():
    """It reaches no step, so M5's second place, which needs a trailing step,
    does not apply: its values are loading values."""
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, None), name="loss"),
        r(3, "lifecycle.stopped", _stopped(True, None, 0, 1, [], 2.0)),
    ]
    assert _synth(rows) == Synth(
        commits={1: []}, exits={1: ()}, left={2: Uncommitted.LOADING}
    )


def test_m5s_third_case_synthesizes_nothing_beside_a_newer_claims_real_beat():
    """Review Focus 3: only in the newest claim before the resuming claim to
    reach the step, the claim M1 will choose wherever its commit there is a
    candidate, as B's step 2 is."""
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


def test_values_after_an_attributed_death_stay_uncommitted():
    """M4: the terminated a died is attributed from ends its claim, so only the
    step-1 value before it reaches A's step 1, which M5's third place
    synthesizes for B."""
    assert _synth(AFTER_AN_ATTRIBUTED_DEATH) == Synth(
        commits={2: [(0, 3, []), (1, RESUMED, [4])], 7: [(2, 8, [])]},
        exits={},
        left={6: Uncommitted.AFTER_ITS_END},
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


def test_an_unattributed_stop_is_an_exit_node_that_vouches_for_nothing(monkeypatch):
    """The rule for lone stops (Task 13) may leave a stop unattributed: a
    self-report that commits nothing (``values: null``), as an error exit does,
    so its claim's trailing step is not synthesized."""
    import runstate.migrations.v0_3_0_to_v0_4_0 as mod

    monkeypatch.setattr(mod, "_lone_stop_rule", _Unattributed)
    assert _synth(TRAILING) == Synth(
        commits={1: [(0, 3, [2]), (1, 5, [4])]},
        exits={1: None},
        left={6: Uncommitted.UNREACHED},
    )


def test_an_exit_whose_last_step_is_null_vouches_for_no_trailing_step():
    """A ``stopped`` with ``final_step: null`` that the placeholder reads as
    the worker's exit (the "clear staged stop" default body, say): its exit
    reached no step, so every trailing value of its claim is above it."""
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "value", _value(0.8, 1, 3.0), name="loss"),
        r(4, "lifecycle.stopped", _stopped(False, None, None, 1, [], 4.0)),
    ]
    assert _synth(rows) == Synth(
        commits={1: [(0, 2, [])]},
        exits={1: ()},
        left={3: Uncommitted.ABOVE_THE_LAST_STEP},
    )


def test_a_retrys_loading_status_reaches_no_step():
    """A retry that crashed while loading wrote its status at step 0; C resumed
    from A's step 0. The retry beat nothing, so it did not reach step 0 and
    nothing is synthesized in it: A is the claim M1 will choose."""
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
        left={5: Uncommitted.LOADING},
    )


def _synthetics(rows):
    """``{(claim, old step): (place, previous, t, consumed_seq)}`` of every
    synthetic commit, ``previous`` the seq of the real heartbeat before it."""
    return {
        (c.claim.started.seq, k.step): (
            k.origin.place,
            k.origin.previous.seq,
            k.origin.t,
            k.origin.previous.consumed_seq,
        )
        for c in _synthesis(rows).claims
        for k in c.commits
        if isinstance(k.origin, Synthetic)
    }


def test_m8_dates_a_synthetic_commit_by_its_last_dated_value_else_the_beat_before():
    """``t`` from the last value it commits that carries one, else from the real
    heartbeat before it; ``consumed_seq`` from that heartbeat."""
    assert _synthetics(SKIPPED) == {
        (1, 2): (SKIP, 5, 4.0, 4),
        (1, 4): (SKIP, 9, 5.0, 8),
    }
    assert _synthetics(TRAILING) == {(1, 2): (CLEAN, 5, 4.0, 4)}
    assert _synthetics(RESUMED_PAST_A_LOADING_CRASH) == {(1, 2): (RESUMED, 5, 4.0, 4)}


def test_the_newest_claim_to_reach_a_step_is_found_in_claim_order():
    """In claim order, never by step nearness. M1 asks it of its candidates, M5's
    third place of every step a claim reached."""
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
    """Real commits are exactly the claim's integer-step heartbeats, and a claim
    with none commits nothing. Commits run in step order, gapless from the first
    real beat to the last. A skipped tick sits between them; a trailing step
    sits above the last and holds values, and is in one of M5's two other
    places, each restated here from the parsed log: at or below the last step of
    the worker's own non-error exit, or one step below the first real beat of a
    later claim. A synthetic commit's ``previous`` is its claim's newest real
    beat below it."""
    log = parse(GOLDEN[rid])
    ends = classify(log).ends
    first: dict[int, int] = {}  # each claim's first integer step, by its seq
    for b in log.beats:
        if b.step is not None:
            first.setdefault(b.claim_seq, b.step)
    for c in _synthesis(GOLDEN[rid]).claims:
        claim = c.claim.started.seq
        steps = [k.step for k in c.commits]
        assert steps == sorted(set(steps))
        real = [k.origin.beat for k in c.commits if isinstance(k.origin, Real)]
        assert real == [b for b in c.claim.beats if b.step is not None]
        assert all(
            k.step == k.origin.beat.step
            for k in c.commits
            if isinstance(k.origin, Real)
        )
        if not real:
            assert c.commits == ()
            continue
        assert set(range(real[0].step, real[-1].step + 1)) <= set(steps)
        for k in c.commits:
            if not isinstance(k.origin, Synthetic):
                continue
            assert k.origin.previous == [b for b in real if b.step < k.step][-1]
            if k.origin.place is SKIP:
                assert real[0].step < k.step < real[-1].step
                continue
            assert k.values and k.step > real[-1].step
            if k.origin.place is CLEAN:
                end = ends.get(claim)
                assert isinstance(end, _Exit) and end.stop.error is None
                assert end.stop.final_step is not None
                assert k.step <= end.stop.final_step
            else:
                assert k.origin.place is RESUMED
                later = [x.seq for x in log.claims if x.seq > claim]
                assert any(first.get(x) == k.step + 1 for x in later)


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_no_commit_holds_a_value_after_its_claims_end(rid):
    """M4, whatever ended the claim: the first stopped naming it, or the
    terminated a died is attributed from. A stepped value after it is left as
    after its end."""
    p = run_passes(GOLDEN[rid])
    ends = {claim: end.stop.seq for claim, end in p.classification.ends.items()}
    ends |= {d.death.claim: seq for seq, d in p.reports.deaths.items()}
    why = {x.value.seq: x.why for x in p.synthesis.uncommitted}
    for c in p.synthesis.claims:
        end = ends.get(c.claim.started.seq)
        if end is not None:
            assert all(seq < end for k in c.commits for seq in k.values)
            after = [
                v.seq for v in c.claim.values if v.seq > end and v.step is not None
            ]
            assert all(why[seq] is Uncommitted.AFTER_ITS_END for seq in after)


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


# ----- pass 4: parents and resume points (M1) ----------------------------------------------


class Links(NamedTuple):
    """A linkage as a test reads it, by pass 3's identity, a commit being its
    (claim seq, old step): each claim's resume point, each commit's parent, and
    each exit node's parent by its claim."""

    resume_from: dict[int, ResumeFrom]
    parents: dict[tuple[int, int], tuple[int, int] | None]
    exits: dict[int, tuple[int, int] | None]


def _links(rows):
    out = run_passes(rows).linkage
    return Links(
        resume_from=dict(out.resume_from),
        parents=dict(out.parents),
        exits=dict(out.exits),
    )


def test_the_displacement_parent_history_parents_in_claim_order():
    """Review Focus 2: C parents on B's step 2, the newest claim to hold one,
    though A's step-2 beat is the later record."""
    beats = parse(DISPLACEMENT_PARENT).beats
    latest = [b for b in beats if b.step == 2 and b.seq < 13][-1]
    assert latest.claim_seq == 1  # the golden discriminates: by position, A's
    assert _links(DISPLACEMENT_PARENT) == Links(
        resume_from={1: Fresh(), 6: Resumed((1, 1)), 13: Resumed((6, 2))},
        parents={
            (1, 0): None,
            (1, 1): (1, 0),
            (1, 2): (1, 1),
            (6, 2): (1, 1),
            (13, 3): (6, 2),
        },
        exits={1: (1, 2), 6: (6, 2), 13: (13, 3)},
    )


_B_TAIL = (6, 7, 10)  # B's step-2 value, its step-2 beat and its exit
_A_TAIL = (8, 9, 11)  # A's displaced step-2 value and beat, and its exit


def _displacement_reordered(a_at):
    """DISPLACEMENT_PARENT with A's and B's records after B's claim interleaved,
    A's at positions ``a_at`` of the six, each writer's own order kept."""
    a = iter([DISPLACEMENT_PARENT[i] for i in _A_TAIL])
    b = iter([DISPLACEMENT_PARENT[i] for i in _B_TAIL])
    middle = [next(a) if k in a_at else next(b) for k in range(6)]
    rows = DISPLACEMENT_PARENT[:6] + middle + DISPLACEMENT_PARENT[12:]
    return _dated([x._replace(seq=n) for n, x in enumerate(rows, 1)])


@pytest.mark.parametrize("a_at", list(itertools.combinations(range(6), 3)))
def test_m1_parents_in_claim_order_however_the_displaced_worker_interleaves(a_at):
    """Review Focus 2, under every reordering of the two writers: positions
    across writers are never compared."""
    links = _links(_displacement_reordered(a_at))
    assert links.resume_from[6] == Resumed((1, 1))
    assert links.resume_from[13] == Resumed((6, 2))
    assert links.parents[(13, 3)] == (6, 2)


def test_m1_takes_the_newest_claim_holding_the_step_not_the_nearest_end():
    """Never by step nearness: A ended at step 1 and B, a fresh restart, went on
    to step 3; C resumed from step 1, which both hold. B is the newer."""
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(4, "lifecycle.stopped", _stopped(False, None, 1, 1, [], 4.0)),
        r(5, "lifecycle.started", _started("local://n1/12", 5.0)),
        *[r(6 + s, "lifecycle.heartbeat", _beat(s, 0, 5, 6.0 + s)) for s in range(4)],
        r(10, "lifecycle.stopped", _stopped(False, None, 3, 5, [], 10.0)),
        r(11, "lifecycle.started", _started("local://n1/13", 11.0)),
        r(12, "lifecycle.heartbeat", _beat(2, 0, 11, 12.0)),
    ]
    links = _links(rows)
    assert links.resume_from == {
        1: Fresh(),
        5: Fresh(),  # a first commit at step 0 resumes from nothing
        11: Resumed((5, 1)),
    }
    assert links.exits == {1: (1, 1), 5: (5, 3)}  # C has no end, so no exit


def test_a_commitless_claim_resumes_from_an_inferred_commit():
    assert _links(COMMITLESS) == Links(
        resume_from={
            1: Fresh(),
            5: Inferred((1, 1)),
            11: Resumed((1, 1)),
        },
        parents={(1, 0): None, (1, 1): (1, 0), (11, 2): (1, 1)},
        exits={1: (1, 1), 5: (1, 1), 11: (11, 2)},
    )


def test_a_commitless_claim_infers_from_the_newest_claim_in_claim_order():
    """B displaced A and beat 2; A, still running, beat 2 and 3 after it. C beat
    nothing: its resume point is B's newest commit, though A's step 3 is both
    the later record and the higher step."""
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(4, "lifecycle.started", _started("local://n2/22", 4.0)),
        r(5, "lifecycle.heartbeat", _beat(2, 0, 4, 5.0)),
        r(6, "lifecycle.heartbeat", _beat(2, 0, 1, 5.5)),  # A's
        r(7, "lifecycle.heartbeat", _beat(3, 0, 1, 6.0)),  # A's
        r(8, "lifecycle.stopped", _stopped(False, None, 2, 4, [], 7.0)),
        r(9, "lifecycle.stopped", _stopped(False, None, 3, 1, [], 7.5)),  # A's
        r(10, "lifecycle.started", _started("local://n1/13", 8.0)),
        r(11, "value", _value(LOADING_STATUS, 0, 8.5), name="status"),
        r(12, "lifecycle.stopped", _stopped(False, "OSError", None, 10, [], 9.0)),
    ]
    links = _links(rows)
    assert links.resume_from[10] == Inferred((4, 2))
    assert links.exits[10] == (4, 2)


def test_a_displaced_workers_step_after_the_claim_is_no_inferred_resume_point():
    """The ruling on candidates: A's step 2 is placed above B's started."""
    assert _links(LOADING_RETRY_BESIDE_A_DISPLACED_WORKER) == Links(
        resume_from={1: Fresh(), 4: Inferred((1, 1))},
        parents={(1, 0): None, (1, 1): (1, 0), (1, 2): (1, 1)},
        exits={4: (1, 1)},
    )


def test_a_resume_from_a_step_committed_after_the_claim_is_parentless():
    """A known limit: B loaded a checkpoint A saved after B claimed, so the
    only step-2 commit is no candidate."""
    assert _links(RESUMED_FROM_A_STEP_COMMITTED_AFTER_THE_CLAIM) == Links(
        resume_from={1: Fresh(), 4: Parentless()},
        parents={(1, 0): None, (1, 1): (1, 0), (1, 2): (1, 1), (4, 3): None},
        exits={4: (4, 3)},
    )


def test_m1_takes_an_older_claims_candidate_below_a_newer_claims_later_step():
    assert _links(RESUMED_ON_AN_OLDER_CLAIMS_STEP) == Links(
        resume_from={1: Fresh(), 6: Resumed((1, 0)), 8: Resumed((1, 2))},
        parents={
            (1, 0): None,
            (1, 1): (1, 0),
            (1, 2): (1, 1),
            (6, 1): (1, 0),
            (6, 2): (6, 1),
            (8, 3): (1, 2),
        },
        exits={1: (1, 2), 8: (8, 3)},
    )


def test_a_synthetic_commit_waits_for_the_beat_before_it():
    """A synthetic commit is placed after its parent: A's skipped tick at step 2
    follows A's step-1 beat, which follows B's started, so it is no candidate."""
    assert _synth(RESUMED_FROM_A_TICK_SKIPPED_AFTER_THE_CLAIM).commits[1] == [
        (0, 2, []),
        (1, 4, []),
        (2, SKIP, []),
        (3, 5, []),
    ]
    assert _links(RESUMED_FROM_A_TICK_SKIPPED_AFTER_THE_CLAIM).resume_from == {
        1: Fresh(),
        3: Parentless(),
    }


def test_a_skipped_tick_is_decided_by_the_beat_before_it_not_the_one_after():
    """A's skipped tick at step 2 follows A's step-1 beat, which precedes B's
    started, so it is a candidate, though A's step-3 beat, the real one after
    it, follows B's started: B resumes from it."""
    assert _links(RESUMED_FROM_A_TICK_SKIPPED_ACROSS_THE_CLAIM) == Links(
        resume_from={1: Fresh(), 4: Resumed((1, 2))},
        parents={
            (1, 0): None,
            (1, 1): (1, 0),
            (1, 2): (1, 1),
            (1, 3): (1, 2),
            (4, 3): (1, 2),
        },
        exits={4: (4, 3)},
    )


def test_pass_3_and_pass_4_choose_apart_past_a_doubly_displaced_worker():
    """A known limit: B's step 3 is synthesized for C, but the beat before it,
    B's step 2, follows C's started, so it is no candidate. C's first commit is a
    parentless resume, and nothing in this log continues B's step 3."""
    links = _links(DOUBLY_DISPLACED)
    assert _synth(DOUBLY_DISPLACED).commits[5] == [
        (1, 7, []),
        (2, 9, []),
        (3, RESUMED, [6]),
    ]
    assert links == Links(
        resume_from={1: Fresh(), 5: Resumed((1, 0)), 8: Parentless()},
        parents={
            (1, 0): None,
            (1, 1): (1, 0),
            (1, 2): (1, 1),
            (5, 1): (1, 0),
            (5, 2): (5, 1),
            (5, 3): (5, 2),
            (8, 4): None,
        },
        exits={},
    )


def test_a_commitless_first_claim_infers_no_resume_point():
    """The inference finds no commit before it: its resume point, and its exit's
    parent, are null."""
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(LOADING_STATUS, 0, 1.5), name="status"),
        r(3, "lifecycle.stopped", _stopped(False, "OSError", None, 1, [], 2.0)),
    ]
    assert _links(rows) == Links(
        resume_from={1: Inferred(None)}, parents={}, exits={1: None}
    )


def test_a_parentless_resume_is_named():
    assert _links(RESUMED_PAST_AN_UNRECORDED_STEP) == Links(
        resume_from={1: Fresh(), 7: Parentless()},
        parents={(1, 0): None, (1, 1): (1, 0), (7, 3): None, (7, 4): (7, 3)},
        exits={7: (7, 4)},  # A's end is a release, no exit node
    )


def test_a_resume_past_a_preempted_exit_parents_on_the_commit():
    """Review §12, item 14: A's exit and B's first commit both parent on A's
    step-1 commit."""
    assert _links(RESUMED_PAST_A_PREEMPTED_EXIT) == Links(
        resume_from={1: Fresh(), 8: Resumed((1, 1))},
        parents={(1, 0): None, (1, 1): (1, 0), (8, 2): (1, 1)},
        exits={1: (1, 1), 8: (8, 2)},
    )


def test_a_resume_past_a_trailing_step_parents_on_the_synthesized_commit():
    """M1 reads after synthesis: A's preempted exit vouches for its trailing step
    1 (M5's second place), and B parents on that commit."""
    rows = PREEMPTED + [
        r(6, "lifecycle.started", _started("local://n1/12", 5.0)),
        r(7, "lifecycle.heartbeat", _beat(2, 0, 6, 6.0)),
    ]
    assert _links(rows) == Links(
        resume_from={1: Fresh(), 6: Resumed((1, 1))},
        parents={(1, 0): None, (1, 1): (1, 0), (6, 2): (1, 1)},
        exits={1: (1, 1)},
    )


def test_a_displaced_workers_branch_keeps_its_claims_parents():
    assert _links(DISPLACED) == Links(
        resume_from={1: Fresh(), 5: Resumed((1, 1))},
        parents={
            (1, 0): None,
            (1, 1): (1, 0),
            (1, 2): (1, 1),
            (5, 2): (1, 1),
            (5, 3): (5, 2),
        },
        exits={1: (1, 2), 5: (5, 3)},
    )


@pytest.mark.parametrize(
    "rid", ["resumed_past_a_loading_crash", "loading_retry_at_its_resume_step"]
)
def test_a_loading_retry_infers_its_resume_point_after_synthesis(rid):
    """B beat nothing: its resume point is A's newest commit, step 2, which M5's
    third place synthesized for C; before synthesis it would be step 1. C, past
    B, parents on the same commit."""
    links = _links(GOLDEN[rid])
    assert links.resume_from == {
        1: Fresh(),
        8: Inferred((1, 2)),
        11: Resumed((1, 2)),
    }
    assert links.parents[(11, 3)] == (1, 2)


@pytest.mark.parametrize("rid", ["loading_retry_released", "loading_retry_unended"])
def test_a_loading_retry_nothing_ended_or_a_third_party_released_has_no_exit(rid):
    assert _links(GOLDEN[rid]) == Links(
        resume_from={1: Fresh(), 5: Inferred((1, 0)), 8: Resumed((1, 0))},
        parents={(1, 0): None, (8, 1): (1, 0)},
        exits={1: (1, 0), 8: (8, 1)},
    )


def test_a_beatless_claim_that_finished_a_step_leaves_a_parentless_resume():
    """The cost pass 3 names: no claim holds step 2, so C's first commit has no
    parent, while B's resume point is inferred."""
    assert _links(BEATLESS_FULL_STEP).resume_from == {
        1: Fresh(),
        5: Inferred((1, 1)),
        9: Parentless(),
    }


def test_m1_parents_on_the_newer_claims_real_beat_beside_a_crashed_step():
    assert _links(RESUMED_BESIDE_A_REAL_BEAT).resume_from == {
        1: Fresh(),
        8: Resumed((1, 1)),
        12: Resumed((8, 2)),
    }


def _ref(c, k):
    return CommitRef(c.claim.started.seq, k.step)


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_a_claims_commits_chain_from_its_resume_point_to_its_exit(rid):
    """Every claim has one resume point and every commit one parent. A claim's
    first commit parents on its resume point, each later one on its previous
    commit, and its exit node on its position: its newest commit, else its
    resume point."""
    p = run_passes(GOLDEN[rid])
    s, out = p.synthesis, p.linkage
    assert list(out.resume_from) == [c.claim.started.seq for c in s.claims]
    assert set(out.parents) == {_ref(c, k) for c in s.claims for k in c.commits}
    assert set(out.exits) == {
        c.claim.started.seq for c in s.claims if c.exit is not None
    }
    for c in s.claims:
        claim = c.claim.started.seq
        start = out.resume_from[claim]
        refs = [_ref(c, k) for k in c.commits]
        assert [out.parents[x] for x in refs] == [start.node, *refs][:-1]
        if c.exit is not None:
            assert out.exits[claim] == (refs[-1] if refs else start.node)


def _deciding_beats(s):
    """For every commit, written out apart from ``link``: the seq of its
    claim's newest heartbeat at an integer step at or below its own. A commit
    is a candidate for a claim when this precedes the claim's started."""
    return {
        _ref(c, k): max(
            b.seq for b in c.claim.beats if b.step is not None and b.step <= k.step
        )
        for c in s.claims
        for k in c.commits
    }


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_a_resume_point_is_the_newest_earlier_claims_candidate_one_step_below(rid):
    """Review Focus 2 over every golden log, among the candidates, the commits
    placed below the claim's started: a first commit at step s resumes from the
    candidate at s - 1 of the newest earlier claim, in claim order, to hold
    one, and is a parentless resume only when none does; a claim with no
    commit resumes, by inference, from the newest candidate of the newest
    earlier claim to hold one."""
    p = run_passes(GOLDEN[rid])
    s, out = p.synthesis, p.linkage
    after = _deciding_beats(s)
    for i, c in enumerate(s.claims):
        start = out.resume_from[c.claim.started.seq]
        candidates = [
            [_ref(e, k) for k in e.commits if after[_ref(e, k)] < c.claim.started.seq]
            for e in s.claims[:i]
        ]
        if not c.commits:
            newest = [held[-1] for held in candidates if held]
            assert start == Inferred(newest[-1] if newest else None)
            continue
        below = c.commits[0].step - 1
        holding = [x for held in candidates for x in held if x.step == below]
        if below < 0:
            assert start == Fresh()
        elif holding:
            assert start == Resumed(holding[-1])
        else:
            assert start == Parentless()


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_p1_a_real_commit_linked_back_to_step_0_counts_its_step(rid):
    """P1: a real commit whose chain M1 linked back to old step 0 has s + 1
    commits on it, itself included. Every other chain ends at a parentless
    resume, named."""
    p = run_passes(GOLDEN[rid])
    s, out = p.synthesis, p.linkage
    for c in s.claims:
        for k in c.commits:
            if not isinstance(k.origin, Real):
                continue
            chain = [_ref(c, k)]
            while (parent := out.parents[chain[-1]]) is not None:
                chain.append(parent)
            root = chain[-1]
            if root.step == 0:
                assert out.resume_from[root.claim] == Fresh()
                assert len(chain) == k.step + 1
            else:
                assert out.resume_from[root.claim] == Parentless()


# ----- pass 5: placement and renumbering (M6); pass 6: the renames (M7) -----------------


def _short(key):
    """An output record as a test spells it: an input record by its 0.3.0
    seq, a synthetic commit by its (claim, old step), a withdrawal by the id it
    withdraws, an attributed death by the terminated it follows."""
    match key:
        case Input(seq):
            return seq
        case Synthesized(ref):
            return tuple(ref)
        case Withdrawal(request_id=request_id):
            return f"withdraw {request_id}"
        case Attributed(terminated=terminated):
            return f"died after {terminated}"


def _keys(rows):
    """The output records, in output order."""
    return [_short(o.key) for o in run_passes(rows).outs]


def _commits(s):
    """Pass 3's commits, by their identity."""
    return {_ref(c, k): k for c in s.claims for k in c.commits}


def _consumed(k):
    return (
        k.origin.beat.consumed_seq
        if isinstance(k.origin, Real)
        else k.origin.previous.consumed_seq
    )


def test_a_commit_moves_after_a_value_that_follows_its_heartbeat():
    """M2's post-loop value: step 1's commit follows its final eval, and goes
    just after it, before the register and the exit."""
    assert _keys(POST_LOOP) == [1, 2, 3, 4, 6, 5, 7, 8]


def test_a_synthetic_commit_goes_just_after_its_last_value_else_its_parent():
    """Step 2's, with values, after its last value; step 4's, with none, after
    its parent, step 3's commit. The real commits stay at their heartbeats."""
    assert _keys(SKIPPED) == [1, 2, 3, 4, 5, 6, 7, (1, 2), 8, 9, (1, 4), 10, 11, 12]
    assert _keys(TRAILING) == [1, 2, 3, 4, 5, 6, (1, 2), 7]


def test_a_commit_stays_at_its_heartbeat_when_everything_it_names_precedes_it():
    """M6 as the migration reads it: a commit only moves forward. A's step 2
    names nothing after its parent, A's step 1, yet stays at its heartbeat,
    after B's started: a displaced worker's iteration is never placed before
    the claim that displaced it. A's step 0 moves after its value."""
    assert _keys(DISPLACED) == [1, 3, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    assert _keys(RESUMED_FROM_A_STEP_COMMITTED_AFTER_THE_CLAIM) == [1, 2, 3, 4, 5, 6, 7]


def test_a_commit_follows_its_parent_past_its_own_heartbeat():
    """A late eval of step 0 moves step 0's commit past step 1's heartbeat, so
    step 1's commit moves after its parent."""
    rows = _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
            r(3, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
            r(4, "value", _value(0.95, 0, 4.0), name="eval"),
            r(5, "lifecycle.stopped", _stopped(True, None, 1, 1, [], 5.0)),
        ]
    )
    assert _keys(rows) == [1, 4, 2, 3, 5]


def _m6_by_list(rows):
    """M6 written apart from ``place``: the output as a list, each commit
    moved in claim order to just after the latest record it must follow."""
    p = run_passes(rows)
    s, links = p.synthesis, p.linkage
    order = [x.seq for x in rows]
    item = {}  # each commit's item in the list: its heartbeat's seq, or its ref
    for c in s.claims:
        for k in c.commits:
            ref = _ref(c, k)
            real = isinstance(k.origin, Real)
            item[ref] = k.origin.beat.seq if real else tuple(ref)
            follows = list(k.values[-1:])
            if links.parents[ref] is not None:
                follows.append(item[links.parents[ref]])
            if _consumed(k):
                follows.append(_consumed(k))
            if real:
                follows.append(item[ref])
            latest = max(follows, key=order.index)
            if latest != item[ref]:
                if real:
                    order.remove(item[ref])
                order.insert(order.index(latest) + 1, item[ref])
    return order


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_m6_places_every_commit_as_a_list_written_apart_does(rid):
    """The withdrawals, the stops they replace and the attributed deaths set
    apart: M6 places commits only."""
    rows = GOLDEN[rid]
    dropped = {k.seq for k in run_passes(rows).placement.dropped}
    later = ("withdraw", "died after")
    assert [k for k in _keys(rows) if not str(k).startswith(later)] == [
        x for x in _m6_by_list(rows) if x not in dropped
    ]


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_the_renumbering_is_a_bijection_on_the_inputs_records(rid):
    """Every input record but a dropped stop, a real commit's heartbeat
    included, has exactly one 0.4.0 seq, and the synthetic commits, the
    withdrawals and the attributed deaths fill the rest, gapless from 1."""
    rows = GOLDEN[rid]
    lay = run_passes(rows)
    synthetic = {
        ref
        for ref, k in _commits(lay.synthesis).items()
        if isinstance(k.origin, Synthetic)
    }
    withdrawals = {
        Withdrawal(seq, i)
        for seq, x in lay.reports.stops.items()
        for i in x.stop.honored
    }
    attributed = {Attributed(seq) for seq in lay.reports.deaths}
    kept = {Input(x.seq) for x in rows} - lay.placement.dropped
    added = len(synthetic) + len(withdrawals) + len(attributed)
    assert sorted(lay.numbering.values()) == list(range(1, len(kept) + added + 1))
    assert {k for k in lay.numbering if isinstance(k, Input)} == kept
    assert {k.ref for k in lay.numbering if isinstance(k, Synthesized)} == synthetic
    assert {k for k in lay.numbering if isinstance(k, Withdrawal)} == withdrawals
    assert {k for k in lay.numbering if isinstance(k, Attributed)} == attributed
    assert [(o.seq, o.key) for o in lay.outs] == sorted(
        ((n, k) for k, n in lay.numbering.items()), key=lambda x: x[0]
    )


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_a_commit_only_moves_forward(rid):
    """Every input record but a moved commit or a dropped stop keeps its order,
    and a real commit follows every record that preceded its heartbeat."""
    rows = GOLDEN[rid]
    lay = run_passes(rows)
    new = {k.seq: n for k, n in lay.numbering.items() if isinstance(k, Input)}
    kept = [
        new[x.seq]
        for x in rows
        if x.seq in new and Input(x.seq) not in lay.placement.moves
    ]
    assert kept == sorted(kept)
    for k in _commits(lay.synthesis).values():
        if isinstance(k.origin, Real):
            beat = k.origin.beat.seq
            assert all(new[s] < new[beat] for s in range(1, beat) if s in new)


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_every_record_follows_what_it_names(rid):
    """§7's "Also": 0 commits name a later value, 0 parents follow their child,
    and 0 commits precede the record their consumed_seq names. Every other
    seq-valued field names an earlier record too: a claim_seq, a resume_from
    and an exit's parent."""
    for o in run_passes(GOLDEN[rid]).outs:
        match o.record:
            case ToCommit():
                assert all(v < o.seq for v in o.record.values)
                assert o.record.parent is None or o.record.parent < o.seq
                assert o.record.consumed_seq < o.seq
                assert o.record.claim_seq < o.seq
            case ToHeartbeat():
                assert o.record.consumed_seq < o.seq and o.record.claim_seq < o.seq
            case ToStarted():
                assert o.record.resume_from is None or o.record.resume_from < o.seq
            case ToExit():
                assert o.record.claim_seq < o.seq
                assert o.record.parent is None or o.record.parent < o.seq
            case ToDied():
                assert o.record.claim_seq < o.seq


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="the placeholder rule for lone stops reads a third party's lone stop "
    "as its claim's exit, so the worker's later beat is a commit the exit's "
    "parent names; the census's rule (Task 13) owns it, and once it reads the "
    "stop as a third party's this passes, which strict makes loud",
)
def test_an_exit_the_placeholder_reads_before_the_workers_beat_has_no_later_parent():
    rows = _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
            r(3, "lifecycle.stopped", _stopped(False, None, 0, 1, [], 3.0)),
            r(4, "lifecycle.heartbeat", _beat(1, 0, 1, 4.0)),  # still running
        ]
    )
    for o in run_passes(rows).outs:
        if isinstance(o.record, ToExit):
            assert o.record.parent is None or o.record.parent < o.seq


def _expected(key, rows):
    """The kind of output record an input record becomes, by its topic, or a
    record a pass adds."""
    if isinstance(key, Synthesized):
        return ToCommit
    if isinstance(key, Withdrawal):
        return ToWithdraw
    if isinstance(key, Attributed):
        return ToDied
    x = rows[key.seq - 1]
    if x.topic == "lifecycle.heartbeat":
        return ToHeartbeat if json.loads(x.body)["step"] is None else ToCommit
    return {
        "lifecycle.started": ToStarted,
        "lifecycle.stopped": (ToExit, ToDied, ToReap),
        "value": ToValue,
    }.get(x.topic, NoSeqs)


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_every_record_with_a_seq_valued_field_is_renamed(rid):
    """A record whose 0.3.0 body holds a seq becomes a kind that renames it;
    only a record that holds none passes as it is."""
    rows = GOLDEN[rid]
    for o in run_passes(rows).outs:
        assert isinstance(o.record, _expected(o.key, rows))
        if isinstance(o.record, NoSeqs):
            assert o.record.record.topic not in {
                "lifecycle.started",
                "lifecycle.heartbeat",
                "lifecycle.stopped",
                "value",
            }


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_review_focus_1_every_seq_valued_field_names_the_record_it_named(rid):
    """Review Focus 1: after M6, each field names the very record it named in
    0.3.0, or that pass 4 chose, and so a record of the expected kind: a
    claim_seq names a started, a parent or resume_from a commit, a values entry
    a value, and a consumed_seq the record it named, 0 staying 0."""
    rows = GOLDEN[rid]
    lay = run_passes(rows)
    at = {o.seq: o for o in lay.outs}
    commits = _commits(lay.synthesis)

    def claim(seq):
        assert isinstance(at[seq].record, ToStarted)
        return at[seq].record.started.seq

    def node(seq):
        if seq is None:
            return None
        assert isinstance(at[seq].record, ToCommit)
        return at[seq].record.ref

    def was(seq):
        assert isinstance(at[seq].key, Input)
        return at[seq].key.seq

    def watermark(seq):
        return 0 if seq == 0 else was(seq)

    for o in lay.outs:
        match o.record:
            case ToStarted(started=started, resume_from=resume_from):
                assert node(resume_from) == lay.linkage.resume_from[started.seq].node
            case ToCommit(ref=ref):
                k = commits[ref]
                assert claim(o.record.claim_seq) == ref.claim
                assert watermark(o.record.consumed_seq) == _consumed(k)
                assert node(o.record.parent) == lay.linkage.parents[ref]
                assert [was(v) for v in o.record.values] == list(k.values)
                assert all(isinstance(at[v].record, ToValue) for v in o.record.values)
            case ToHeartbeat(beat=beat):
                assert claim(o.record.claim_seq) == beat.claim_seq
                assert watermark(o.record.consumed_seq) == beat.consumed_seq
            case ToExit(node=exit):
                assert claim(o.record.claim_seq) == exit.stop.claim_seq
                assert node(o.record.parent) == lay.linkage.exits[exit.stop.claim_seq]
            case ToDied(death=death):
                assert claim(o.record.claim_seq) == death.claim


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_the_candidates_are_exactly_the_commits_placed_below_each_started(rid):
    """Task 5's ruling holds exactly under M6: a commit of an earlier claim is
    placed below a claim's started when its deciding beat precedes it, and
    only then."""
    lay = run_passes(GOLDEN[rid])
    s = lay.synthesis
    after = _deciding_beats(s)
    for i, c in enumerate(s.claims):
        started = lay.numbering[Input(c.claim.started.seq)]
        for e in s.claims[:i]:
            for k in e.commits:
                ref = _ref(e, k)
                placed = lay.numbering[lay.placement.commits[ref]]
                assert (placed < started) == (after[ref] < c.claim.started.seq)


def test_a_synthetic_commit_takes_the_created_at_of_the_record_it_is_placed_after():
    """Every input record keeps its own; a synthetic commit takes its anchor's."""
    lay = run_passes(SKIPPED)
    at = {o.seq: o for o in lay.outs}
    assert [
        (o.key.ref, o.created_at) for o in lay.outs if isinstance(o.key, Synthesized)
    ] == [((1, 2), 1001.75), ((1, 4), 1002.25)]
    for o in lay.outs:
        if isinstance(o.key, Input):
            assert o.created_at == SKIPPED[o.key.seq - 1].created_at
        else:
            assert o.created_at == at[o.seq - 1].created_at


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_a_synthetic_commit_is_placed_after_a_record_it_names(rid):
    lay = run_passes(GOLDEN[rid])
    for key, at in lay.placement.moves.items():
        assert isinstance(at, After)
        if isinstance(key, Synthesized):
            k = _commits(lay.synthesis)[key.ref]
            parent = lay.placement.commits[lay.linkage.parents[key.ref]]
            assert at.anchor in {Input(v) for v in k.values} | {parent}


def test_a_watermark_is_renamed_with_the_record_it_names():
    lay = run_passes(WATERMARKED)
    assert [_short(o.key) for o in lay.outs] == [1, 2, (1, 1), 3, 4, 5, 6, 7]
    beat, commit = lay.outs[5].record, lay.outs[6].record
    assert isinstance(beat, ToHeartbeat) and isinstance(commit, ToCommit)
    assert (beat.consumed_seq, commit.consumed_seq) == (5, 5)
    assert lay.outs[4].record.record.request_id == "halt"


def _ref_key(claim, step):
    return Synthesized(CommitRef(claim, step))


# Records placed by any pass, as the withdrawals are: two after one anchor, a
# third after one of those, and an input record moved after another.
PLACED = {
    _ref_key(9, 1): After(Input(1), (2,)),
    _ref_key(9, 2): After(Input(1), (1,)),
    _ref_key(9, 3): After(_ref_key(9, 1), (0,)),
    Input(2): After(Input(3), (0,)),
}


def test_records_placed_after_one_anchor_follow_it_in_tie_order_each_with_its_own():
    assert list(renumber([1, 2, 3], PLACED, frozenset()).items()) == [
        (Input(1), 1),
        (_ref_key(9, 2), 2),
        (_ref_key(9, 1), 3),
        (_ref_key(9, 3), 4),
        (Input(3), 5),
        (Input(2), 6),
    ]


def test_records_placed_after_a_dropped_record_take_its_place():
    """A third party's stop that becomes only its withdrawals."""
    assert list(renumber([1, 2, 3], PLACED, frozenset({Input(1)})).items()) == [
        (_ref_key(9, 2), 1),
        (_ref_key(9, 1), 2),
        (_ref_key(9, 3), 3),
        (Input(3), 4),
        (Input(2), 5),
    ]


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_m6_places_at_most_one_record_after_any_record(rid):
    """A commit is placed after its last value, which is its own, or after its
    parent, which only its claim's next commit follows: M6's ties never
    decide, and only a later pass's can."""
    moves = run_passes(GOLDEN[rid]).placement.moves
    commits = [
        at.anchor for k, at in moves.items() if isinstance(k, Input | Synthesized)
    ]
    assert len(set(commits)) == len(commits)


def test_a_stop_at_n_request_id_stays_as_it_is():
    """Request ids are strings: the stop at seq 3, renumbered 2, keeps its id,
    and the exit still honors it by that id."""
    rows = _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
            r(3, "control.stop", {}, rid="stop@3"),
            r(4, "value", _value(0.9, 0, 3.0), name="loss"),
            r(5, "lifecycle.stopped", _stopped(False, None, 0, 1, ["stop@3"], 4.0)),
        ]
    )
    lay = run_passes(rows)
    assert [_short(o.key) for o in lay.outs] == [1, 3, 4, 2, 5]
    stop, exit = lay.outs[1], lay.outs[4]
    assert (stop.seq, stop.record.record.request_id) == (2, "stop@3")
    assert exit.record.node.stop.honored == ("stop@3",)


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
        ("value", _value(0.5, 0, 1.0)),  # M9: a null name
        ("control.subscribe", {}, "s"),  # M9: a subscription
        ("lifecycle.bound", {"claim_seq": 1}, "s"),  # M9: a subscription's binding
        ("control.stop", {"from": {"step": 3}}, "h"),  # M9: a step atom
        ("launcher.terminated", {"reason": "exited"}, "L1"),  # not launcher-v0.4's
    ],
)
def test_a_refusal_names_the_first_record_the_step_cannot_read(first):
    """In seq order, whatever the topics: a malformed claim after it does not
    pre-empt it, and M9 refuses at the boundary too."""
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
        step.transform(rows)


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
        step.transform(rows)


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


def _two_refusals(drained_at, unattributable_at):
    """A watermark naming a stop that leaves the log, and a launch no
    launched names, at the seqs given (5 or 6 each, distinct)."""
    rows = {
        1: r(1, "lifecycle.started", _started(H, 1.0)),
        2: r(2, "lifecycle.stopped", _stopped(True, None, None, 1, [], 2.0)),
        3: r(3, "lifecycle.stopped", _stopped(True, None, None, 1, [], 3.0)),
        4: r(4, "lifecycle.started", _started(H, 4.0), rid="L2"),
        drained_at: r(drained_at, "lifecycle.heartbeat", _beat(None, 3, 4, 5.0)),
        unattributable_at: r(
            unattributable_at,
            "launcher.terminated",
            _terminated("exited", 1, None, 6.0),
            rid="L2",
        ),
    }
    return [rows[s] for s in sorted(rows)]


@pytest.mark.parametrize("drained_at, unattributable_at", [(5, 6), (6, 5)])
def test_a_run_is_refused_by_its_lowest_refused_seq(drained_at, unattributable_at):
    """Of the refusals that need the whole log, the run's names the lowest seq.
    The order holds within a pass, not across passes: parse refuses first, the
    first record it cannot carry, before any pass that needs the whole log."""
    rows = _two_refusals(drained_at, unattributable_at)
    with pytest.raises(MigrationError, match=r"^the \S+ at seq 5 "):
        step.transform(rows)


# ----- the 0.4.0 rows: the records, and third parties' stops -----------------------------
#
# Each golden's output rows, as (seq, topic, name, request_id, body): 0.4.0's
# bodies (lifecycle-v0.6, subscription-v0.4, supervisor-v0.5, value-v0.3), every
# field present, null or [] where it does not apply.

MIGRATED = "migrated: third-party stopped"  # a death's evidence when nothing says more


def o(seq, topic, body, rid=None, name=None):
    return (seq, topic, name, rid, body)


def _claim4(handle, t, resume_from):
    return {"handle": handle, "t": t, "resume_from": resume_from, "sources": []}


def _commit4(claim_seq, consumed_seq, parent, values, t):
    return {
        "claim_seq": claim_seq,
        "consumed_seq": consumed_seq,
        "parent": parent,
        "values": values,
        "fired": [],
        "expired": [],
        "t": t,
    }


def _beat4(claim_seq, consumed_seq, t):
    return {"claim_seq": claim_seq, "consumed_seq": consumed_seq, "t": t}


def _exit4(completed, error, claim_seq, honored, parent, values, t):
    return {
        "completed": completed,
        "error": error,
        "claim_seq": claim_seq,
        "honored": honored,
        "parent": parent,
        "values": values,
        "fired": [],
        "expired": [],
        "t": t,
    }


def _value4(value, t):
    return {"value": value, "t": t}


def _died4(claim_seq, outcome, evidence, t):
    return {"claim_seq": claim_seq, "outcome": outcome, "evidence": evidence, "t": t}


def _terminated4(reason, exit_code, signal, t):
    return {"reason": reason, "exit_code": exit_code, "signal": signal, "t": t}


def _rows(rows):
    return [
        (x.seq, x.topic, x.name, x.request_id, json.loads(x.body))
        for x in step.transform(rows)
    ]


START, COMMIT, BEAT, STOPPED = (
    "lifecycle.started",
    "lifecycle.commit",
    "lifecycle.heartbeat",
    "lifecycle.stopped",
)
DIED, TERMINATED, WITHDRAW = (
    "supervisor.died",
    "supervisor.terminated",
    "control.withdraw",
)


LOADING_4 = {"phase": "LOADING", "status": "loading enwiki"}
DONE_4 = {"phase": "DONE", "status": "done"}
MAX_STEPS_4 = {"reason": "max_steps"}

# Every golden's 0.4.0 rows, derived by hand from its comment and §7, exactly.
OUTPUT = {
    # A displaced worker's records are written for its own claim: A's step 0
    # moves after its value; A's step 2 stays at its beat, after B's started.
    "displaced": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, COMMIT, _commit4(1, 0, 3, [], 3.0)),
        o(5, START, _claim4("local://n2/22", 5.0, 4)),
        o(6, "value", _value4(0.8, 6.0), name="loss"),
        o(7, COMMIT, _commit4(1, 0, 4, [], 6.0)),
        o(8, COMMIT, _commit4(5, 0, 4, [6], 7.0)),
        o(9, "value", _value4(0.7, 8.0), name="loss"),
        o(10, COMMIT, _commit4(5, 0, 8, [9], 8.0)),
        o(11, STOPPED, _exit4(False, None, 1, [], 7, [], 9.0)),
        o(12, STOPPED, _exit4(True, None, 5, [], 10, [], 10.0)),
    ],
    # A worker's exit is the exit node, and keeps what it honored.
    "exit": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, "control.stop", {}, rid="halt"),
        o(4, STOPPED, _exit4(False, None, 1, ["halt"], 2, [], 3.0)),
    ],
    # Review Focus 4: the backstop's crash is the claim's death; the default
    # body after it has no error, so only its withdrawal, in its place.
    "backstopped": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, DIED, _died4(1, "errored", BACKSTOP, 3.0)),
        o(4, "control.stop", {}, rid="staged"),
        o(5, WITHDRAW, {"t": 4.0}, rid="staged"),
    ],
    # Owner ruling c': a backstop after its claim's end is a terminated, under
    # a minted, labelled launch id.
    "late_backstop": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, STOPPED, _exit4(False, None, 1, [], 2, [], 3.0)),
        o(4, TERMINATED, _terminated4("exited", 1, None, 4.0), rid="backstop@4"),
    ],
    # M10: died preempted, its reason the evidence, dated by its created_at; its
    # honored stop withdrawn after it. The repair is a later stop with no error
    # and nothing honored: nothing. A's first commit, at step 4, has no claim
    # before it: a parentless resume.
    "reclaimed": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, "control.stop", {}, rid="stop@3"),
        o(4, DIED, _died4(1, "preempted", "reclaimed: slurm TIMEOUT", 1001.0)),
        o(5, WITHDRAW, {"t": 1001.0}, rid="stop@3"),
    ],
    # A claimless stop is only its withdrawals.
    "claimless": [
        o(1, "control.stop", {}, rid="pre"),
        o(2, WITHDRAW, {"t": 1.0}, rid="pre"),
    ],
    # The fresh claim's step-0 status is its step-0 commit's (M2); the resumed
    # claim's is a leading value of its first commit, at step 2, which resumes
    # from A's step 1.
    "loading": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(LOADING_4, 1.5), name="status"),
        o(3, "value", _value4(0.9, 2.0), name="loss"),
        o(4, COMMIT, _commit4(1, 0, None, [2, 3], 2.0)),
        o(5, "value", _value4(0.8, 3.0), name="loss"),
        o(6, COMMIT, _commit4(1, 0, 4, [5], 3.0)),
        o(7, STOPPED, _exit4(False, None, 1, [], 6, [], 4.0)),
        o(8, START, _claim4("local://n1/12", 5.0, 6)),
        o(9, "value", _value4(LOADING_4, 5.5), name="status"),
        o(10, "value", _value4(0.7, 6.0), name="loss"),
        o(11, COMMIT, _commit4(8, 0, 6, [9, 10], 6.0)),
        o(12, STOPPED, _exit4(True, None, 8, [], 11, [], 7.0)),
    ],
    # M8: step 2's synthetic commit dated by its last dated value, step 4's by
    # the beat before it, each with that beat's watermark.
    "skipped": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(1.0, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, "value", _value4(0.9, 3.0), name="loss"),
        o(5, COMMIT, _commit4(1, 4, 3, [4], 3.0)),
        o(6, "value", _value4(0.8, 4.0), name="loss"),
        o(7, "value", _value4(0.5, None), name="acc"),
        o(8, COMMIT, _commit4(1, 4, 5, [6, 7], 4.0)),
        o(9, "value", _value4(0.7, 5.0), name="loss"),
        o(10, COMMIT, _commit4(1, 9, 8, [9], 5.0)),
        o(11, COMMIT, _commit4(1, 9, 10, [], 5.0)),
        o(12, "value", _value4(0.5, 7.0), name="loss"),
        o(13, COMMIT, _commit4(1, 12, 11, [12], 7.0)),
        o(14, STOPPED, _exit4(True, None, 1, [], 13, [], 8.0)),
    ],
    # M2: step 1's commit moves after the final eval it commits, before the
    # register (M3) and the exit.
    "post_loop": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, "value", _value4(0.8, 3.0), name="loss"),
        o(5, "value", _value4(0.95, 4.0), name="final_eval"),
        o(6, COMMIT, _commit4(1, 0, 3, [4, 5], 3.0)),
        o(7, "value", _value4(MAX_STEPS_4, 4.5), name="reason"),
        o(8, STOPPED, _exit4(True, None, 1, [], 6, [], 5.0)),
    ],
    # M5's second place: step 2 is synthesized after its value, dated by it,
    # with step 1's watermark; the exit holds nothing.
    "trailing": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, "value", _value4(0.8, 3.0), name="loss"),
        o(5, COMMIT, _commit4(1, 4, 3, [4], 3.0)),
        o(6, "value", _value4(0.7, 4.0), name="loss"),
        o(7, COMMIT, _commit4(1, 4, 5, [6], 4.0)),
        o(8, STOPPED, _exit4(True, None, 1, [], 7, [], 5.0)),
    ],
    # A preempted exit vouches for its trailing step 1 too.
    "preempted": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, "value", _value4(0.8, 3.0), name="loss"),
        o(5, COMMIT, _commit4(1, 0, 3, [4], 3.0)),
        o(6, STOPPED, _exit4(False, None, 1, [], 5, [], 4.0)),
    ],
    # An error exit holds null, and its trailing step stays uncommitted.
    "errored": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, "value", _value4(0.8, 3.0), name="loss"),
        o(5, STOPPED, _exit4(False, "CUDA out of memory", 1, [], 3, None, 4.0)),
    ],
    # M5's third place synthesizes A's step 2 after its value; A's error exit,
    # B (inferred) and C (by M1) all stand on it.
    "resumed_past_a_loading_crash": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, "value", _value4(0.8, 3.0), name="loss"),
        o(5, COMMIT, _commit4(1, 4, 3, [4], 3.0)),
        o(6, "value", _value4(0.7, 4.0), name="loss"),
        o(7, COMMIT, _commit4(1, 4, 5, [6], 4.0)),
        o(8, STOPPED, _exit4(False, "CUDA out of memory", 1, [], 7, None, 5.0)),
        o(9, START, _claim4("local://n1/12", 6.0, 7)),
        o(10, "value", _value4(LOADING_4, 6.5), name="status"),
        o(11, STOPPED, _exit4(False, "OSError", 9, [], 7, None, 7.0)),
        o(12, START, _claim4("local://n1/13", 8.0, 7)),
        o(13, "value", _value4(0.6, 9.0), name="loss"),
        o(14, COMMIT, _commit4(12, 0, 7, [13], 9.0)),
        o(15, STOPPED, _exit4(True, None, 12, [], 14, [], 10.0)),
    ],
    # Nothing is synthesized beside B's real step 2: A's step-2 value stays
    # uncommitted, and C resumes from B's step 2.
    "resumed_beside_a_real_beat": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, "value", _value4(0.8, 3.0), name="loss"),
        o(5, COMMIT, _commit4(1, 0, 3, [4], 3.0)),
        o(6, "value", _value4(0.7, 4.0), name="loss"),
        o(7, STOPPED, _exit4(False, "CUDA out of memory", 1, [], 5, None, 5.0)),
        o(8, START, _claim4("local://n1/12", 6.0, 5)),
        o(9, "value", _value4(0.71, 7.0), name="loss"),
        o(10, COMMIT, _commit4(8, 0, 5, [9], 7.0)),
        o(11, STOPPED, _exit4(False, None, 8, [], 10, [], 8.0)),
        o(12, START, _claim4("local://n1/13", 9.0, 10)),
        o(13, "value", _value4(0.6, 10.0), name="loss"),
        o(14, COMMIT, _commit4(12, 0, 10, [13], 10.0)),
        o(15, STOPPED, _exit4(True, None, 12, [], 14, [], 11.0)),
    ],
    # M4 and M3: the teardown status after the exit and the register stay
    # uncommitted.
    "after_stopped": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, STOPPED, _exit4(True, None, 1, [], 3, [], 3.0)),
        o(5, "value", _value4(DONE_4, 4.0), name="status"),
        o(6, "value", _value4(MAX_STEPS_4, 4.5), name="reason"),
    ],
    # A's step-3 value, which pass 1 gives B, is above B's exit's last step:
    # uncommitted. B resumes from A's step 1; A's displaced step 2 follows it.
    "above": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, COMMIT, _commit4(1, 0, 2, [], 3.0)),
        o(4, START, _claim4("local://n2/22", 4.0, 3)),
        o(5, COMMIT, _commit4(4, 0, 3, [], 5.0)),
        o(6, COMMIT, _commit4(1, 0, 3, [], 5.5)),
        o(7, "value", _value4(0.5, 6.0), name="loss"),
        o(8, STOPPED, _exit4(True, None, 4, [], 5, [], 7.0)),
        o(9, STOPPED, _exit4(False, None, 1, [], 6, [], 8.0)),
    ],
    # The backstop releases B, which beat nothing: a death; C resumes from A's
    # step 0, as B's inferred resume point does.
    "loading_retry_released": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, STOPPED, _exit4(False, "boom", 1, [], 3, None, 3.0)),
        o(5, START, _claim4("local://n1/12", 4.0, 3)),
        o(6, "value", _value4(LOADING_4, 4.5), name="status"),
        o(7, DIED, _died4(5, "errored", BACKSTOP, 5.0)),
        o(8, START, _claim4("local://n1/13", 6.0, 3)),
        o(9, "value", _value4(0.8, 7.0), name="loss"),
        o(10, COMMIT, _commit4(8, 0, 3, [9], 7.0)),
        o(11, STOPPED, _exit4(True, None, 8, [], 10, [], 8.0)),
    ],
    # Nothing ends B: a user's record passes as it is.
    "loading_retry_unended": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, STOPPED, _exit4(False, "boom", 1, [], 3, None, 3.0)),
        o(5, START, _claim4("local://n1/12", 4.0, 3)),
        o(6, "value", _value4(LOADING_4, 4.5), name="status"),
        o(7, "user.note", {"killed": True}),
        o(8, START, _claim4("local://n1/13", 6.0, 3)),
        o(9, "value", _value4(0.8, 7.0), name="loss"),
        o(10, COMMIT, _commit4(8, 0, 3, [9], 7.0)),
        o(11, STOPPED, _exit4(True, None, 8, [], 10, [], 8.0)),
    ],
    # B's status at step 2 is loading; A takes the synthesis of step 2, which
    # B (inferred) and C (by M1) stand on.
    "loading_retry_at_its_resume_step": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, "value", _value4(0.8, 3.0), name="loss"),
        o(5, COMMIT, _commit4(1, 4, 3, [4], 3.0)),
        o(6, "value", _value4(0.7, 4.0), name="loss"),
        o(7, COMMIT, _commit4(1, 4, 5, [6], 4.0)),
        o(8, STOPPED, _exit4(False, "CUDA out of memory", 1, [], 7, None, 5.0)),
        o(9, START, _claim4("local://n1/12", 6.0, 7)),
        o(10, "value", _value4(LOADING_4, 6.5), name="status"),
        o(11, DIED, _died4(9, "errored", BACKSTOP, 7.0)),
        o(12, START, _claim4("local://n1/13", 8.0, 7)),
        o(13, "value", _value4(0.6, 9.0), name="loss"),
        o(14, COMMIT, _commit4(12, 0, 7, [13], 9.0)),
        o(15, STOPPED, _exit4(True, None, 12, [], 14, [], 10.0)),
    ],
    # B's step-2 values stay loading; nothing holds step 2, so C is a
    # parentless resume.
    "beatless_full_step": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, COMMIT, _commit4(1, 0, 2, [], 3.0)),
        o(4, STOPPED, _exit4(False, None, 1, [], 3, [], 4.0)),
        o(5, START, _claim4("local://n1/12", 5.0, 3)),
        o(6, "value", _value4(0.7, 6.0), name="loss"),
        o(7, "value", _value4({"status": "aligning step 2"}, 6.5), name="status"),
        o(8, DIED, _died4(5, "errored", BACKSTOP, 7.0)),
        o(9, START, _claim4("local://n1/13", 8.0, None)),
        o(10, "value", _value4(0.6, 9.0), name="loss"),
        o(11, COMMIT, _commit4(9, 0, None, [10], 9.0)),
        o(12, STOPPED, _exit4(True, None, 9, [], 11, [], 10.0)),
    ],
    # Review Focus 2: C parents on B's step 2. B's step 2 commits A's displaced
    # value too (M2), and moves after it.
    "displacement_parent": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, "value", _value4(0.8, 3.0), name="loss"),
        o(5, COMMIT, _commit4(1, 0, 3, [4], 3.0)),
        o(6, START, _claim4("local://n2/22", 5.0, 5)),
        o(7, "value", _value4(0.7, 6.0), name="loss"),
        o(8, "value", _value4(0.71, 6.5), name="loss"),
        o(9, COMMIT, _commit4(6, 0, 5, [7, 8], 6.0)),
        o(10, COMMIT, _commit4(1, 0, 5, [], 6.5)),
        o(11, STOPPED, _exit4(False, None, 6, [], 9, [], 7.0)),
        o(12, STOPPED, _exit4(False, None, 1, [], 10, [], 7.5)),
        o(13, START, _claim4("local://n3/33", 8.0, 9)),
        o(14, "value", _value4(0.6, 9.0), name="loss"),
        o(15, COMMIT, _commit4(13, 0, 9, [14], 9.0)),
        o(16, STOPPED, _exit4(True, None, 13, [], 15, [], 10.0)),
    ],
    # A stepless heartbeat is a heartbeat without step; B, commitless, resumes
    # by inference from A's step 1, as C does by M1.
    "commitless": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, COMMIT, _commit4(1, 0, 2, [], 3.0)),
        o(4, STOPPED, _exit4(False, None, 1, [], 3, [], 4.0)),
        o(5, START, _claim4("local://n1/12", 5.0, 3)),
        o(6, "value", _value4({"status": "loading"}, 5.5), name="status"),
        o(7, BEAT, _beat4(5, 0, 6.0)),
        o(8, "value", _value4({"status": "waiting"}, 6.5), name="status"),
        o(9, BEAT, _beat4(5, 0, 7.0)),
        o(10, STOPPED, _exit4(False, None, 5, [], 3, [], 8.0)),
        o(11, START, _claim4("local://n1/13", 9.0, 3)),
        o(12, COMMIT, _commit4(11, 0, 3, [], 10.0)),
        o(13, STOPPED, _exit4(True, None, 11, [], 12, [], 11.0)),
    ],
    # The backstop releases A; B is a parentless resume.
    "resumed_past_an_unrecorded_step": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, "value", _value4(0.8, 3.0), name="loss"),
        o(5, COMMIT, _commit4(1, 0, 3, [4], 3.0)),
        o(6, DIED, _died4(1, "errored", BACKSTOP, 4.0)),
        o(7, START, _claim4("local://n1/12", 5.0, None)),
        o(8, "value", _value4(0.6, 6.0), name="loss"),
        o(9, COMMIT, _commit4(7, 0, None, [8], 6.0)),
        o(10, "value", _value4(0.5, 7.0), name="loss"),
        o(11, COMMIT, _commit4(7, 0, 9, [10], 7.0)),
        o(12, STOPPED, _exit4(True, None, 7, [], 11, [], 8.0)),
    ],
    # A's exit and B's first commit both parent on A's step 1.
    "resumed_past_a_preempted_exit": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, "value", _value4(0.8, 3.0), name="loss"),
        o(5, COMMIT, _commit4(1, 0, 3, [4], 3.0)),
        o(6, "control.stop", {}, rid="preempt"),
        o(7, STOPPED, _exit4(False, None, 1, ["preempt"], 5, [], 4.0)),
        o(8, START, _claim4("local://n1/12", 5.0, 5)),
        o(9, "value", _value4(0.7, 6.0), name="loss"),
        o(10, COMMIT, _commit4(8, 0, 5, [9], 6.0)),
        o(11, STOPPED, _exit4(True, None, 8, [], 10, [], 7.0)),
    ],
    # A's step 2 follows B's started: B infers A's step 1.
    "loading_retry_beside_a_displaced_worker": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, COMMIT, _commit4(1, 0, 2, [], 3.0)),
        o(4, START, _claim4("local://n2/22", 4.0, 3)),
        o(5, STOPPED, _exit4(False, "OSError", 4, [], 3, None, 5.0)),
        o(6, COMMIT, _commit4(1, 0, 3, [], 6.0)),
    ],
    # A's step 2 follows B's started, so B is a parentless resume.
    "resumed_from_a_step_committed_after_the_claim": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, COMMIT, _commit4(1, 0, 2, [], 3.0)),
        o(4, START, _claim4("local://n2/22", 4.0, None)),
        o(5, COMMIT, _commit4(1, 0, 3, [], 5.0)),
        o(6, COMMIT, _commit4(4, 0, None, [], 6.0)),
        o(7, STOPPED, _exit4(True, None, 4, [], 6, [], 7.0)),
    ],
    # B branches from A's step 0; C takes A's step 2, B's being placed above
    # C's started.
    "resumed_on_an_older_claims_step": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, COMMIT, _commit4(1, 0, 2, [], 3.0)),
        o(4, COMMIT, _commit4(1, 0, 3, [], 4.0)),
        o(5, STOPPED, _exit4(False, None, 1, [], 4, [], 5.0)),
        o(6, START, _claim4("local://n2/22", 6.0, 2)),
        o(7, COMMIT, _commit4(6, 0, 2, [], 7.0)),
        o(8, START, _claim4("local://n3/33", 8.0, 4)),
        o(9, COMMIT, _commit4(6, 0, 7, [], 9.0)),
        o(10, COMMIT, _commit4(8, 0, 4, [], 10.0)),
        o(11, STOPPED, _exit4(True, None, 8, [], 10, [], 11.0)),
    ],
    # B's step 3 is synthesized for C after its parent, B's step 2, dated by
    # A's displaced value it commits; C is a parentless resume.
    "doubly_displaced": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, COMMIT, _commit4(1, 0, 2, [], 3.0)),
        o(4, COMMIT, _commit4(1, 0, 3, [], 4.0)),
        o(5, START, _claim4("local://n2/22", 5.0, 2)),
        o(6, "value", _value4(0.5, 6.0), name="loss"),
        o(7, COMMIT, _commit4(5, 0, 2, [], 7.0)),
        o(8, START, _claim4("local://n3/33", 8.0, None)),
        o(9, COMMIT, _commit4(5, 0, 7, [], 9.0)),
        o(10, COMMIT, _commit4(5, 0, 9, [6], 6.0)),
        o(11, COMMIT, _commit4(8, 0, None, [], 10.0)),
    ],
    # A's skipped tick at step 2 is synthesized after its parent, A's step 1,
    # dated by that beat; B is a parentless resume.
    "resumed_from_a_tick_skipped_after_the_claim": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, START, _claim4("local://n2/22", 3.0, None)),
        o(4, COMMIT, _commit4(1, 0, 2, [], 4.0)),
        o(5, COMMIT, _commit4(1, 0, 4, [], 4.0)),
        o(6, COMMIT, _commit4(1, 0, 5, [], 5.0)),
        o(7, COMMIT, _commit4(3, 0, None, [], 6.0)),
        o(8, STOPPED, _exit4(True, None, 3, [], 7, [], 7.0)),
    ],
    # A's skipped step 2 goes just after its parent, A's step 1, below B's
    # started, dated by that beat: B resumes from it, and so does B's first
    # commit. A's step 3, after B's started, parents on it too.
    "resumed_from_a_tick_skipped_across_the_claim": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, COMMIT, _commit4(1, 0, 2, [], 3.0)),
        o(4, COMMIT, _commit4(1, 0, 3, [], 3.0)),
        o(5, START, _claim4("local://n2/22", 4.0, 4)),
        o(6, COMMIT, _commit4(1, 0, 4, [], 5.0)),
        o(7, COMMIT, _commit4(5, 0, 4, [], 6.0)),
        o(8, STOPPED, _exit4(True, None, 5, [], 7, [], 7.0)),
    ],
    # A stepless heartbeat's watermark is renamed with the stop it names.
    "watermarked": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, COMMIT, _commit4(1, 0, 2, [], 2.0)),
        o(4, COMMIT, _commit4(1, 0, 3, [], 3.0)),
        o(5, "control.stop", {}, rid="halt"),
        o(6, BEAT, _beat4(1, 5, 4.0)),
        o(7, COMMIT, _commit4(1, 5, 4, [], 5.0)),
        o(8, STOPPED, _exit4(False, None, 1, ["halt"], 7, [], 6.0)),
    ],
    # A later stop with its error set is a death.
    "later_error": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, STOPPED, _exit4(False, None, 1, [], 2, [], 3.0)),
        o(4, DIED, _died4(1, "errored", "slurm: NODE_FAIL", 4.0)),
    ],
    # A later stop with no error, completed or not, is only its withdrawals: a
    # death is never a completion, and once the claim has ended only a report
    # with a cause is written.
    "later_default": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, "control.stop", {}, rid="halt"),
        o(4, STOPPED, _exit4(False, None, 1, [], 2, [], 3.0)),
        o(5, WITHDRAW, {"t": 4.0}, rid="halt"),
    ],
    # A later M10 body is only its withdrawals, dated by its created_at.
    "later_release": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, "control.stop", {}, rid="stop@3"),
        o(4, STOPPED, _exit4(False, None, 1, [], 2, [], 3.0)),
        o(5, WITHDRAW, {"t": 1001.25}, rid="stop@3"),
    ],
    # A later stop with its error set is a death in its place, then its
    # withdrawal after it, dated by it.
    "later_error_honored": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, "control.stop", {}, rid="halt"),
        o(4, STOPPED, _exit4(False, None, 1, [], 2, [], 3.0)),
        o(5, DIED, _died4(1, "errored", "slurm: NODE_FAIL", 4.0)),
        o(6, WITHDRAW, {"t": 4.0}, rid="halt"),
    ],
    # A claimless stop with its error set names no claim to die: only its
    # withdrawal.
    "claimless_error": [
        o(1, "control.stop", {}, rid="pre"),
        o(2, WITHDRAW, {"t": 1.0}, rid="pre"),
    ],
    # M10 naming no claim: only its withdrawal, dated by its created_at.
    "claimless_release": [
        o(1, "control.stop", {}, rid="pre"),
        o(2, WITHDRAW, {"t": 1000.5}, rid="pre"),
    ],
    # Owner ruling c': a claimless backstop is a terminated, killed by the
    # signal its negative N names, and its withdrawal after it.
    "claimless_backstop": [
        o(1, "control.stop", {}, rid="pre"),
        o(2, TERMINATED, _terminated4("killed", None, 9, 1.0), rid="backstop@2"),
        o(3, WITHDRAW, {"t": 1.0}, rid="pre"),
        o(4, START, _claim4("local://n1/11", 2.0, None)),
    ],
    # Owner ruling c': the first backstop is the claim's death, the second a
    # terminated, so the claim has one death.
    "second_backstop": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, DIED, _died4(1, "errored", BACKSTOP, 3.0)),
        o(4, TERMINATED, _terminated4("exited", 137, None, 4.0), rid="backstop@4"),
    ],
    # Owner ruling c': the minted launch id is fresh against the run's ids.
    "backstop_id_taken": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, "control.stop", {}, rid="backstop@5"),
        o(4, STOPPED, _exit4(False, None, 1, ["backstop@5"], 2, [], 3.0)),
        o(5, TERMINATED, _terminated4("exited", 1, None, 4.0), rid="backstop@5~2"),
    ],
    # launcher.* moves to supervisor.*, its bodies unchanged.
    "launched": [
        o(
            1,
            "supervisor.launched",
            {"handle": "local://n1/11", "status": "running", "t": 0.5},
            rid="L1",
        ),
        o(2, START, _claim4("local://n1/11", 1.0, None), rid="L1"),
        o(3, COMMIT, _commit4(2, 0, None, [], 2.0)),
        o(4, STOPPED, _exit4(True, None, 2, [], 3, [], 3.0)),
        o(
            5,
            TERMINATED,
            {"reason": "exited", "exit_code": 0, "signal": None, "t": 4.0},
            rid="L1",
        ),
    ],
    # §7: the launch's handle is the claim's, so a died just after its
    # terminated, killed by its signal, at the terminated's t.
    "attributed": [
        o(1, "supervisor.launched", _launched("local://n1/11", 0.5), rid="L1"),
        o(2, START, _claim4("local://n1/11", 1.0, None), rid="L1"),
        o(3, COMMIT, _commit4(2, 0, None, [], 2.0)),
        o(4, TERMINATED, _terminated4("killed", None, 9, 4.0), rid="L1"),
        o(5, DIED, _died4(2, "killed", "signal 9", 4.0)),
    ],
    # A wrapper's exit says nothing of its worker: no died.
    "wrapper": [
        o(1, "supervisor.launched", _launched("local://n1/100", 0.5), rid="L1"),
        o(2, START, _claim4("local://n1/101", 1.0, None), rid="L1"),
        o(3, COMMIT, _commit4(2, 0, None, [], 2.0)),
        o(4, TERMINATED, _terminated4("exited", 0, None, 4.0), rid="L1"),
    ],
    # The claim's pid on another host: no died.
    "another_host": [
        o(1, "supervisor.launched", _launched("local://n2/11", 0.5), rid="L1"),
        o(2, START, _claim4("local://n1/11", 1.0, None), rid="L1"),
        o(3, COMMIT, _commit4(2, 0, None, [], 2.0)),
        o(4, TERMINATED, _terminated4("exited", 1, None, 4.0), rid="L1"),
    ],
    # A's late reap: errored by exit 1, after A's terminated, though B claimed
    # since. B resumes from A's step 0 and its exit is its own end.
    "late_reap": [
        o(1, START, _claim4("local://n1/11", 1.0, None), rid="L1"),
        o(2, "supervisor.launched", _launched("local://n1/11", 1.5), rid="L1"),
        o(3, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(4, "supervisor.launched", _launched("local://n1/12", 3.0), rid="L2"),
        o(5, START, _claim4("local://n1/12", 4.0, 3), rid="L2"),
        o(6, COMMIT, _commit4(5, 0, 3, [], 5.0)),
        o(7, STOPPED, _exit4(True, None, 5, [], 6, [], 6.0)),
        o(8, TERMINATED, _terminated4("exited", 1, None, 7.0), rid="L1"),
        o(9, DIED, _died4(1, "errored", "exit 1", 7.0)),
        o(10, TERMINATED, _terminated4("exited", 0, None, 8.0), rid="L2"),
    ],
    # Only the newest claim naming the launch dies, lost by the exit 0; no
    # death names the older one, as 0.3.0 left it.
    "two_claims_one_launch": [
        o(1, "supervisor.launched", _launched("local://n1/11", 0.5), rid="L1"),
        o(2, START, _claim4("local://n1/11", 1.0, None), rid="L1"),
        o(3, COMMIT, _commit4(2, 0, None, [], 2.0)),
        o(4, START, _claim4("local://n1/11", 3.0, 3), rid="L1"),
        o(5, COMMIT, _commit4(4, 0, 3, [], 4.0)),
        o(6, TERMINATED, _terminated4("exited", 0, None, 5.0), rid="L1"),
        o(7, DIED, _died4(4, "lost", "exit 0", 5.0)),
    ],
    # M4: A's terminated ends A, so A's step 1, synthesized for B, commits only
    # the value before it, and goes just after that value; the value after the
    # reap stays uncommitted. B resumes from A's step 1.
    "after_an_attributed_death": [
        o(1, "supervisor.launched", _launched("local://n1/11", 0.5), rid="L1"),
        o(2, START, _claim4("local://n1/11", 1.0, None), rid="L1"),
        o(3, COMMIT, _commit4(2, 0, None, [], 2.0)),
        o(4, "value", _value4(0.5, 3.0), name="loss"),
        o(5, COMMIT, _commit4(2, 0, 3, [4], 3.0)),
        o(6, TERMINATED, _terminated4("killed", None, 9, 4.0), rid="L1"),
        o(7, DIED, _died4(2, "killed", "signal 9", 4.0)),
        o(8, "value", _value4(0.4, 5.0), name="loss"),
        o(9, START, _claim4("local://n1/12", 6.0, 5)),
        o(10, COMMIT, _commit4(9, 0, 5, [], 7.0)),
    ],
}


def test_every_output_entry_is_a_goldens():
    """A stale entry, whose golden was renamed or removed, fails here."""
    assert set(OUTPUT) == set(GOLDEN)


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_transform_is_deterministic(rid):
    """SQLite calls it twice, the dry run before the seal and the run after."""
    assert step.transform(list(GOLDEN[rid])) == step.transform(list(GOLDEN[rid]))


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_every_goldens_output_rows_are_pinned_exactly(rid):
    """Seq, topic, name, request_id and body of every row. A golden with no
    entry in OUTPUT fails here."""
    assert _rows(GOLDEN[rid]) == OUTPUT[rid]


def test_launcher_records_keep_their_bodies_byte_for_byte():
    out = step.transform(LAUNCHED)
    assert [out[0].body, out[4].body] == [LAUNCHED[0].body, LAUNCHED[4].body]


def test_review_focus_4_a_backstop_then_a_default_body_is_one_errored_death():
    """The verdict, the claim's death reports with no exit, reads {errored},
    not {errored, preempted}: no exit node, one death."""
    rows = _rows(BACKSTOPPED)
    assert not [x for x in rows if x[1] == STOPPED]
    deaths = [x[4]["outcome"] for x in rows if x[1] == DIED and x[4]["claim_seq"] == 1]
    assert deaths == ["errored"]


def test_withdrawals_follow_their_stop_in_honored_order_each_dated_by_it():
    rows = _dated(
        [
            r(1, "control.stop", {}, rid="b"),
            r(2, "control.stop", {}, rid="a"),
            r(
                3,
                "lifecycle.stopped",
                _stopped(False, None, None, None, ["b", "a"], 3.0),
            ),
            r(4, "value", _value({"x": 1}, None, 4.0), name="note"),
        ]
    )
    out = step.transform(rows)
    assert [(x.seq, x.topic, x.request_id, x.created_at) for x in out] == [
        (1, "control.stop", "b", 1000.25),
        (2, "control.stop", "a", 1000.5),
        (3, WITHDRAW, "b", 1000.75),
        (4, WITHDRAW, "a", 1000.75),
        (5, "value", None, 1001.0),
    ]


def test_a_death_or_a_terminated_keeps_its_stops_created_at():
    out = step.transform(CLAIMLESS_BACKSTOP)
    assert [x.created_at for x in out] == [1000.25, 1000.5, 1000.5, 1000.75]


@pytest.mark.parametrize(
    "stop, outcome, evidence",
    [
        (_stopped(True, None, 0, 1, ["halt"], 3.0), "preempted", MIGRATED),
        (_stopped(False, None, 0, 1, ["halt"], 3.0), "preempted", MIGRATED),
        (_stopped(False, "boom", 0, 1, ["halt"], 3.0), "errored", "boom"),
        (_stopped(False, "", 0, 1, ["halt"], 3.0), "errored", MIGRATED),
    ],
    ids=["completed", "default", "error", "empty_error"],
)
def test_a_third_partys_release_maps_to_a_death(monkeypatch, stop, outcome, evidence):
    """The mapping, for a well-formed release the rule for lone stops (Task 13)
    attributes to a third party: completed included, since a death is never a
    completion; the error text as evidence, else the migration's label, since
    evidence is never empty."""
    import runstate.migrations.v0_3_0_to_v0_4_0 as mod

    monkeypatch.setattr(mod, "_lone_stop_rule", _Release)
    rows = EXIT[:3] + [EXIT[3]._replace(body=json.dumps(stop))]
    assert _rows(rows)[3:] == [
        o(4, DIED, _died4(1, outcome, evidence, 3.0)),
        o(5, WITHDRAW, {"t": 3.0}, rid="halt"),
    ]


def test_an_empty_reason_or_error_leaves_the_label_as_evidence():
    released = RECLAIMED[:3] + [
        RECLAIMED[3]._replace(body=json.dumps(_release("", ["stop@3"], 1)))
    ]
    assert _rows(released)[3] == o(4, DIED, _died4(1, "preempted", MIGRATED, 1001.0))
    later = LATER_ERROR[:3] + [
        LATER_ERROR[3]._replace(body=json.dumps(_stopped(False, "", None, 1, [], 4.0)))
    ]
    assert _rows(later)[3] == o(4, DIED, _died4(1, "errored", MIGRATED, 4.0))


def test_an_unattributable_stop_is_a_self_report_that_commits_nothing(monkeypatch):
    """The lone-stop placeholder's third variant: an exit node with values null,
    though it completed."""
    import runstate.migrations.v0_3_0_to_v0_4_0 as mod

    monkeypatch.setattr(mod, "_lone_stop_rule", _Unattributed)
    assert _rows(TRAILING) == [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, "value", _value4(0.8, 3.0), name="loss"),
        o(5, COMMIT, _commit4(1, 4, 3, [4], 3.0)),
        o(6, "value", _value4(0.7, 4.0), name="loss"),
        o(7, STOPPED, _exit4(True, None, 1, [], 5, None, 5.0)),
    ]


def _schema_errors(row):
    """The 0.4.0 stack's errors for a row: the envelope's, and its convention's
    when 0.4.0 owns its topic; a user topic answers to the envelope alone."""
    pytest.importorskip("jsonschema")
    from test_schema_v0_4_0 import STACK_0_4_0, row_errors

    if row.topic == "value" or row.topic.startswith(
        ("lifecycle.", "control.", "supervisor.")
    ):
        return row_errors(row)
    record = row._asdict() | {"body": json.loads(row.body)}
    del record["created_at"]
    return [e.message for e in STACK_0_4_0.envelope.iter_errors(record)]


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_every_output_row_validates_against_the_0_4_0_schemas(rid):
    out = step.transform(GOLDEN[rid])
    assert [x.seq for x in out] == list(range(1, len(out) + 1))
    errors = {x.seq: _schema_errors(x) for x in out}
    assert {seq: e for seq, e in errors.items() if e} == {}


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_every_exit_and_death_names_a_claim_and_a_claim_exits_once(rid):
    """The prose constraints the schemas cannot check: a died's claim_seq names
    a started (supervisor-v0.5), and a claim has at most one exit node."""
    out = _rows(GOLDEN[rid])
    claims = {x[0] for x in out if x[1] == START}
    exits = [x[4]["claim_seq"] for x in out if x[1] == STOPPED]
    deaths = [x[4]["claim_seq"] for x in out if x[1] == DIED]
    assert len(exits) == len(set(exits))
    assert set(exits) | set(deaths) <= claims


def test_a_stop_with_a_time_atom_is_written_as_it_is():
    rows = [r(1, "control.stop", {"from": {"time_seconds": 5}}, rid="h")]
    assert step.transform(rows) == rows


def test_an_attributed_death_keeps_its_terminateds_created_at():
    """It is placed after the terminated, and takes its anchor's."""
    out = step.transform(ATTRIBUTED)
    assert [x.created_at for x in out] == [1000.25, 1000.5, 1000.75, 1001.0, 1001.0]


@pytest.mark.parametrize(
    "end, deaths",
    [
        (_stopped(False, None, 0, 2, [], 3.0), []),  # its own exit
        (  # a third party's release, whose own death is the only one
            _stopped(False, BACKSTOP, 0, 2, [], 3.0),
            [_died4(2, "errored", BACKSTOP, 3.0)],
        ),
    ],
    ids=["exit", "release"],
)
def test_a_claim_a_stopped_ended_gets_no_attributed_death(end, deaths):
    """§7: only a claim 0.3.0 saw ended only by its launch's terminated."""
    rows = ATTRIBUTED[:3] + [
        r(4, "lifecycle.stopped", end),
        ATTRIBUTED[3]._replace(seq=5),
    ]
    assert [x[4] for x in _rows(rows) if x[1] == DIED] == deaths


def test_a_terminated_before_the_claim_is_not_its_end():
    """0.3.0 read only the records after a claim: a terminated of its launch
    before it ends nothing."""
    rows = [
        r(1, "launcher.launched", _launched("local://n1/11", 0.5), rid="L1"),
        r(2, "launcher.terminated", _terminated("exited", 1, None, 1.0), rid="L1"),
        r(3, "lifecycle.started", _started("local://n1/11", 2.0), rid="L1"),
    ]
    assert DIED not in [x[1] for x in _rows(rows)]


def test_a_terminated_no_held_claim_answers_is_written_as_it_is():
    """Another launch's death, or a hand-run claim's run: nothing to attribute."""
    terminated = {"reason": "exited", "exit_code": 1, "signal": None, "t": 4.0}
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),  # hand-run
        r(2, "launcher.terminated", terminated, rid="L0"),
    ]
    assert _rows(rows) == [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, TERMINATED, terminated, rid="L0"),
    ]


# The envelope fields 0.4.0 pins null that 0.3.0 left open: no reference writer
# set them, and the step cannot carry one.


@pytest.mark.parametrize(
    "topic, body, name, rid",
    [
        ("lifecycle.started", _started("local://n1/12", 1.0), "x", None),
        ("lifecycle.heartbeat", _beat(0, 0, 1, 1.0), "x", None),
        ("lifecycle.heartbeat", _beat(0, 0, 1, 1.0), None, "x"),
        ("lifecycle.stopped", _stopped(False, None, 0, 1, [], 1.0), "x", None),
        ("lifecycle.stopped", _stopped(False, None, 0, 1, [], 1.0), None, "x"),
        ("lifecycle.stopped", _release("x", [], 1), None, "x"),
        ("lifecycle.nak", {"reason": "malformed", "message": ""}, "x", "s"),
        ("control.stop", {}, "x", "h"),
    ],
)
def test_an_envelope_field_0_4_0_pins_null_is_refused_by_seq(topic, body, name, rid):
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, topic, body, rid=rid, name=name),
    ]
    with pytest.raises(MigrationError, match=rf"{topic} at seq 2 carries "):
        parse(rows)
    with pytest.raises(MigrationError, match=rf"{topic} at seq 2 carries "):
        step.transform(rows)


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
    # M9: any subscription, with its samples, its binding and its unsubscribe
    "subscribe": [CLAIM, r(2, "control.subscribe", {}, rid="s", name="loss")],
    "sample": [CLAIM, r(2, "value", _value(0.5, 0, 1.0), rid="s", name="loss")],
    "bound": [CLAIM, r(2, "lifecycle.bound", {"claim_seq": 1}, rid="s")],
    "unsubscribe": [CLAIM, r(2, "control.unsubscribe", {}, rid="s")],
    # M9: a stop whose from holds a step atom, at any depth
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
    # M9: two heartbeats of one step in one claim
    "rewind": [
        CLAIM,
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 3.0)),
    ],
    # M9: a null name, which value-v0.3 cannot hold
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
    # The attributed death report: a launch that not exactly one launched names
    "no_launched": [
        LAUNCHED_CLAIM._replace(seq=1),
        r(2, "launcher.terminated", REAP, rid="L1"),
    ],
    "two_launched": [
        LAUNCH,
        LAUNCH._replace(seq=2),
        LAUNCHED_CLAIM._replace(seq=3),
        r(4, "launcher.terminated", REAP, rid="L1"),
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
    with pytest.raises(
        MigrationError,
        match=rf"^run 'r1': the {re.escape(last.topic)} at seq {last.seq} ",
    ):
        migrate(SqliteStore(tmp_path), ["r1"], to="0.4.0")
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


def _watermark_on(stop_body):
    """A's exit, a third party's later stop, and B's beat whose watermark names
    that stop."""
    return _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
            r(3, "lifecycle.stopped", _stopped(False, None, 0, 1, [], 3.0)),
            r(4, "lifecycle.stopped", stop_body),
            r(5, "lifecycle.started", _started("local://n1/12", 5.0)),
            r(6, "lifecycle.heartbeat", _beat(None, 4, 5, 6.0)),
        ]
    )


def test_a_watermark_naming_a_stop_that_leaves_the_log_is_refused():
    """The later default body becomes only its withdrawals, so nothing is left
    for the watermark to name. No reference writer names a stopped in one."""
    rows = _watermark_on(_stopped(False, None, 0, 1, [], 4.0))
    with pytest.raises(
        MigrationError,
        match=r"heartbeat at seq 6 has consumed_seq 4, which names the "
        r"lifecycle\.stopped at seq 4, a third party's that becomes only its "
        r"withdrawals",
    ):
        step.transform(rows)


def test_a_watermark_naming_a_stop_replaced_in_place_names_its_replacement():
    rows = _watermark_on(_stopped(False, "slurm: NODE_FAIL", 0, 1, [], 4.0))
    assert _rows(rows)[3:] == [
        o(4, DIED, _died4(1, "errored", "slurm: NODE_FAIL", 4.0)),
        o(5, START, _claim4("local://n1/12", 5.0, 2)),
        o(6, BEAT, _beat4(5, 4, 6.0)),
    ]


def test_c_prime_the_minted_label_names_the_0_3_0_backstop():
    """``backstop@<seq>`` is its 0.3.0 seq, as a ``stop@N`` keeps its own: a
    synthetic commit before it moves the terminated to seq 6."""
    rows = _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
            r(3, "lifecycle.heartbeat", _beat(2, 0, 1, 3.0)),
            r(4, "lifecycle.stopped", _stopped(False, None, 2, 1, [], 4.0)),
            r(5, "lifecycle.stopped", _stopped(False, BACKSTOP, 2, 1, [], 5.0)),
        ]
    )
    assert _rows(rows)[-1] == o(
        6, TERMINATED, _terminated4("exited", 1, None, 5.0), rid="backstop@5"
    )
