"""0.4.0 logs that the staged observer plane's reads are tested on
(tests/test_reads.py, runstate/observables.py).

Each is a conforming 0.4.0 log, as (seq, topic, name, request_id, body), written by
hand for one of the 0.3.0 -> 0.4.0 step's golden 0.3.0 logs before the step was
rebuilt on its candidate engine (Task 22). They are fixtures of the reads now, no
longer the step's outputs, which tests/test_migration_goldens.py pins: the reads'
tests need logs whose verdicts and lineages were derived by hand, and these are.
"""

from tests.migration_goldens import BACKSTOP, SACCT, _launched

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
    # The rule for lone stops: written 1,500 s after its t, the reclaim tool's
    # stop is a release, a death dated by its t, preempted with the label; no
    # exit node, so the step-2 value stays uncommitted (an unreached trailing
    # step).
    "reclaim_reported_later": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, COMMIT, _commit4(1, 0, 2, [], 3.0)),
        o(4, "value", _value4(0.7, 4.0), name="loss"),
        o(5, DIED, _died4(1, "preempted", MIGRATED, 5.0)),
    ],
    # Its 10-23 version writes the cause: errored, the cause the evidence.
    "reclaim_of_10_23": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, COMMIT, _commit4(1, 0, 2, [], 3.0)),
        o(4, "value", _value4(0.7, 4.0), name="loss"),
        o(5, DIED, _died4(1, "errored", SACCT, 5.0)),
    ],
    # The claim beats after its stop: a release, a death in the stop's place;
    # the beat after it is a commit placed after the death, its parent step 0's.
    "beaten_after": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, DIED, _died4(1, "preempted", MIGRATED, 3.0)),
        o(4, COMMIT, _commit4(1, 0, 2, [], 4.0)),
    ],
    # 59 s: A's exit node, vouching for its step 0. 61 s: B's death. B resumed
    # from A's step 0.
    "reported_later_boundary": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, COMMIT, _commit4(1, 0, None, [], 2.0)),
        o(3, STOPPED, _exit4(False, None, 1, [], 2, [], 3.0)),
        o(4, START, _claim4("local://n1/12", 4.0, 2)),
        o(5, COMMIT, _commit4(4, 0, 2, [], 5.0)),
        o(6, DIED, _died4(4, "preempted", MIGRATED, 6.0)),
    ],
    # The worker's exit, keeping the stop it honored; its step-1 commit keeps
    # the watermark naming that stop. The registers stay uncommitted.
    "cooperative": [
        o(1, START, _claim4("local://n1/11", 1.0, None)),
        o(2, "value", _value4(0.9, 2.0), name="loss"),
        o(3, COMMIT, _commit4(1, 0, None, [2], 2.0)),
        o(4, "control.stop", {}, rid="timeout"),
        o(5, "value", _value4(0.8, 3.0), name="loss"),
        o(6, COMMIT, _commit4(1, 4, 3, [5], 3.0)),
        o(7, "value", _value4("preempted", 3.5), name="completion_reason"),
        o(8, STOPPED, _exit4(False, None, 1, ["timeout"], 6, [], 4.0)),
        o(9, "value", _value4("done", 4.5), name="status"),
    ],
}
