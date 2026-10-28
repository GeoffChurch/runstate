"""The pure 0.4.0 reads: the head, the lineage, counts and series, the verdict
and the stop fold (commits-and-lineage.md §2, §5; §10's "Nodes and commits",
"Position", "Settledness", "Reads", "Death reports" and "The verdict", the pure
subset).

Every log here is hand-written 0.4.0 (lifecycle-v0.6, subscription-v0.4,
supervisor-v0.5, value-v0.3) in a MemoryChannel, and goes through ``_log``, so
``test_every_log_is_valid_0_4_0`` checks each against the 0.4.0 schemas: every
row is valid except the rows ``UNREADABLE`` names, which a test makes
unreadable on purpose. The migration step's goldens are real 0.4.0 logs too,
and the last tests read some as the corpus runner will."""

from typing import Any, NamedTuple

import pytest

from runstate.channel.memory import MemoryChannel
from runstate.lineage import (
    AtNode,
    Commits,
    DeathReport,
    Element,
    Head,
    LatestClaimHead,
    Outcome04,
    RunResult04,
    Tier,
    _Commit,
    _Died,
    _Rewind,
    _Started,
    _Stopped,
    _Terminated,
    _Value,
    count,
    last_element,
    lineage,
    prefix_len,
    series,
    undischarged_stops,
    verdict,
)
from runstate.observables import MalformedRecordError


class Rec(NamedTuple):
    topic: str
    body: dict[str, Any]
    name: str | None
    request_id: str | None


def started(resume_from, *, sources=(), t=1.0, launch=None):
    body = {
        "handle": "local://n1/11",
        "t": t,
        "resume_from": resume_from,
        "sources": list(sources),
    }
    return Rec("lifecycle.started", body, None, launch)


def commit(claim, parent, values, *, fired=(), t=2.0):
    body = {
        "claim_seq": claim,
        "consumed_seq": 0,
        "parent": parent,
        "values": list(values),
        "fired": list(fired),
        "expired": [],
        "t": t,
    }
    return Rec("lifecycle.commit", body, None, None)


def rewind(claim, resume_from, *, t=3.0):
    body = {"claim_seq": claim, "resume_from": resume_from, "t": t}
    return Rec("lifecycle.rewind", body, None, None)


def stopped(claim, parent, values, *, completed=False, error=None, honored=(), t=4.0):
    body = {
        "completed": completed,
        "error": error,
        "claim_seq": claim,
        "honored": list(honored),
        "parent": parent,
        "values": None if values is None else list(values),
        "fired": [],
        "expired": [],
        "t": t,
    }
    return Rec("lifecycle.stopped", body, None, None)


def beat(claim, *, t=2.5):
    body = {"claim_seq": claim, "consumed_seq": 0, "t": t}
    return Rec("lifecycle.heartbeat", body, None, None)


def value(name, v, *, t=1.5):
    return Rec("value", {"value": v, "t": t}, name, None)


def subscribe(source, request_id):
    return Rec("control.subscribe", {}, source, request_id)


def died(claim, outcome, *, evidence="slurm:TIMEOUT", t=5.0):
    body = {"claim_seq": claim, "outcome": outcome, "evidence": evidence, "t": t}
    return Rec("supervisor.died", body, None, None)


def launched(launch, *, t=0.5):
    body = {"handle": "local://n1/11", "status": "running", "t": t}
    return Rec("supervisor.launched", body, None, launch)


def exited(launch, code, *, t=6.0):
    body = {"reason": "exited", "exit_code": code, "signal": None, "t": t}
    return Rec("supervisor.terminated", body, None, launch)


def killed(launch, signal, *, t=6.0):
    body = {"reason": "killed", "exit_code": None, "signal": signal, "t": t}
    return Rec("supervisor.terminated", body, None, launch)


def stop(request_id):
    return Rec("control.stop", {}, None, request_id)


def withdraw(request_id, *, t=4.5):
    return Rec("control.withdraw", {"t": t}, None, request_id)


def nak(request_id):
    return Rec(
        "lifecycle.nak", {"reason": "unsupported", "message": "no"}, None, request_id
    )


def junk(r, **body):
    """``r`` with its body changed, to make it unreadable."""
    return r._replace(body={**r.body, **body})


def append(ch, rows):
    for seq, r in rows:
        assert (
            ch.send(r.body, topic=r.topic, name=r.name, request_id=r.request_id) == seq
        )


def channel(rows):
    ch = MemoryChannel()
    append(ch, rows)
    return ch


LOGS: dict[str, list[tuple[int, Rec]]] = {}


def _log(label, *rows):
    LOGS[label] = list(rows)
    return LOGS[label]


# ----- the logs ---------------------------------------------------------------------

# A resumes from nothing, B from A's extendable exit, then rewinds to A's
# second commit and closes the new branch.
RESUMED = _log(
    "resumed",
    (1, started(None)),  # claim A, a fresh start
    (2, value("loss", 0.9)),
    (3, commit(1, None, [2])),  # iteration 1
    (4, value("loss", 0.8)),
    (5, commit(1, 3, [4])),  # iteration 2
    (6, value("loss", 0.7)),  # a post-loop value
    (7, stopped(1, 5, [6])),  # A's preempted exit: extendable
    (8, started(7)),  # claim B resumes from the exit
    (9, value("loss", 0.6)),
    (10, commit(8, 7, [9])),  # iteration 3, through the exit
    (11, rewind(8, 5)),  # back to A's iteration 2
    (12, value("loss", 0.5)),
    (13, commit(8, 5, [12])),  # iteration 3 again, on a new branch
    (14, stopped(8, 13, [], completed=True)),  # closed
)

# A dies torn; B resumes from A's commit and has committed nothing yet.
COMMITLESS = _log(
    "commitless",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(1, None, [2])),
    (4, value("loss", 0.8)),  # pending at the error exit
    (5, stopped(1, 3, None, error="CUDA out of memory")),  # torn
    (6, started(3)),  # B, loading
    (7, value("status", "loading")),  # a register: no node names it
    (8, beat(6)),
)
COMMITLESS_ERRORED = _log(
    "commitless, then errored",
    *COMMITLESS,
    (9, stopped(6, 3, None, error="OSError")),
)

# B claims over A and resumes from A's commit; A runs on and completes.
DISPLACED = _log(
    "displaced",
    (1, started(None)),  # A
    (2, value("loss", 0.9)),
    (3, commit(1, None, [2])),
    (4, started(3)),  # B
    (5, value("loss", 0.8)),  # A's, still running
    (6, commit(1, 3, [5])),  # names A's claim
    (7, stopped(1, 6, [], completed=True)),  # A's completed exit
)

TO_THE_ROOT = _log(
    "a rewind to the root",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(1, None, [2])),
    (4, rewind(1, None)),
)
FROM_THE_ROOT = _log(
    "a commit after a rewind to the root",
    *TO_THE_ROOT,
    (5, value("loss", 0.8)),
    (6, commit(1, None, [5])),
)

EXIT_AFTER_A_REWIND = _log(
    "an exit after a rewind",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, commit(1, 2, [])),
    (4, rewind(1, 2)),
    (5, stopped(1, 2, [])),  # the position is the rewind's target
)

UNCOMMITTED = _log(
    "uncommitted values",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(1, None, [2])),
    (4, value("loss", 0.8)),  # pending at the rewind: discarded
    (5, rewind(1, 3)),
    (6, value("loss", 0.7)),
    (7, commit(1, 3, [6])),
    (8, stopped(1, 7, [], completed=True)),
    (9, value("status", "done")),  # after the exit
)

IN_LINEAGE_ORDER = _log(
    "in lineage order",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, value("loss", 0.8)),
    (4, value("loss", 0.7)),
    (5, commit(1, None, [4, 2])),  # the writer's own order: 4, then 2
    (6, commit(1, 5, [3])),  # an older value, committed later
)

# lr is A's source, sampled when q fires, and an eager stream of B's.
KIND_CHANGED = _log(
    "a name that changed kind",
    (1, started(None, sources=["lr"])),
    (2, subscribe("lr", "q")),
    (3, value("lr", 0.1)),  # A's sample
    (4, value("loss", 0.9)),
    (5, commit(1, None, [4, 3], fired=["q"])),  # the pending values, then samples
    (6, stopped(1, 5, [])),
    (7, started(6)),  # B declares no source
    (8, value("lr", 0.05)),
    (9, commit(7, 6, [8])),
)

TWO_BRANCHES = _log(
    "one value on two branches",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(1, None, [2])),
    (4, rewind(1, None)),
    (5, commit(1, None, [2])),  # committed again, on another lineage
)

# --- malformed, each at its stated check (§5, "Well-formedness")

LATER_VALUE = _log(
    "values names a later seq",
    (1, started(None)),
    (2, commit(1, None, [3])),
    (3, value("loss", 0.9)),
    (4, commit(1, 2, [])),
)
LATER_PARENT = _log(
    "a parent at a later seq",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, commit(1, 4, [])),  # names a later, extendable node
    (4, commit(1, 2, [])),
    (5, commit(1, 3, [])),
)
RESUME_LATER = _log(
    "a started resuming from a later node",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, started(4)),
    (4, commit(1, 2, [])),  # the displaced worker's
)
REWIND_LATER = _log(
    "a rewind to a later node",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, rewind(1, 4)),
    (4, commit(1, 2, [])),
)
PARENT_A_VALUE = _log(
    "a parent naming a value",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(1, 2, [])),
)
PARENT_CLOSED = _log(
    "a parent naming a closed exit",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, stopped(1, 2, [], completed=True)),
    (4, started(None)),
    (5, commit(4, 3, [])),
)
PARENT_TORN = _log(
    "a parent naming a torn exit",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, stopped(1, 2, None, error="boom")),
    (4, started(None)),
    (5, commit(4, 3, [])),
)
RESUME_CLOSED = _log(
    "a resume_from naming a closed exit",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, stopped(1, 2, [], completed=True)),
    (4, started(3)),
)
RESUME_TORN = _log(
    "a resume_from naming a torn exit",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, stopped(1, 2, None)),  # torn: it discarded its open iteration
    (4, started(3)),
)
RESUME_A_HEARTBEAT = _log(
    "a resume_from naming a heartbeat",
    (1, started(None)),
    (2, beat(1)),
    (3, started(2)),
)
REWIND_TO_A_VALUE = _log(
    "a rewind naming a value",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, rewind(1, 2)),
)
ROLLBACK = _log(
    "a rollback written as a commit with no rewind",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, commit(1, 2, [])),
    (4, commit(1, 2, [])),  # the claim's position is 3
)
EXIT_BEFORE_THE_REWIND = _log(
    "an exit whose parent is the position before a rewind",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, commit(1, 2, [])),
    (4, rewind(1, 2)),
    (5, stopped(1, 3, [])),
)
TWICE = _log(
    "a seq committed twice along one lineage",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(1, None, [2])),
    (4, commit(1, 3, [2])),
)
NAMES_A_HEARTBEAT = _log(
    "values names a heartbeat",
    (1, started(None)),
    (2, beat(1)),
    (3, commit(1, None, [2])),
)
BAD_VALUE = Rec("value", {"value": 0.9, "step": 0, "t": 2.0}, "loss", None)
BAD_BODY = _log(
    "a malformed value body",
    (1, started(None)),
    (2, BAD_VALUE),
    (3, value("loss", 0.8)),
    (4, commit(1, None, [2, 3])),
)
BAD_BODY_LAST = _log(
    "a malformed value body, last",
    (1, started(None)),
    (2, BAD_VALUE),
    (3, value("loss", 0.8)),
    (4, commit(1, None, [3, 2])),
)
CLAIM_LATER = _log(
    "a node naming a later claim",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(4, None, [2])),  # a record follows what it names
    (4, started(3)),
    (5, commit(4, 3, [])),
)
CLAIM_NOT_A_CLAIM = _log(
    "a node naming a value as its claim",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(2, None, [2])),
)

# --- the head fold's decisions where §2 and §5 say "at most one" or say nothing

UNREADABLE_EXIT = _log(
    "an unreadable exit",
    (1, started(None)),
    (2, commit(1, None, [])),
    (
        3,
        Rec(
            "lifecycle.stopped",
            {**stopped(1, 2, []).body, "completed": "yes"},
            None,
            None,
        ),
    ),
)
TWO_EXITS = _log(
    "two exits of one claim",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, stopped(1, 2, [])),
    (4, stopped(1, 2, [], completed=True)),
)
AFTER_THE_EXIT = _log(
    "a commit after its claim's exit",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, stopped(1, 2, [])),
    (4, commit(1, 2, [])),
)


def nameless(r):
    """``r`` with no ``claim_seq``: a record no fold can attribute."""
    return r._replace(body={k: v for k, v in r.body.items() if k != "claim_seq"})


NAMES_NO_CLAIM = _log(
    "a commit naming no claim",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, nameless(commit(1, 2, []))),
)
DISPLACED_NAMELESS = _log(
    "a displaced worker's nameless commit",
    (1, started(None)),  # A
    (2, commit(1, None, [])),
    (3, commit(1, 2, [])),
    (4, started(3)),  # B
    (5, nameless(commit(1, 3, []))),  # A's, still running
    (6, commit(4, 3, [])),  # B's own
)
OWN_NAMELESS = _log(
    "the claim's own nameless commit, then its next",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, nameless(commit(1, 2, []))),
    (4, commit(1, 3, [])),  # its parent is not the claim's position, 2
)
ANOTHER_CLAIMS_JUNK = _log(
    "another claim's unreadable commit",
    *DISPLACED[:4],
    (5, Rec("lifecycle.commit", {**commit(1, 3, []).body, "values": "x"}, None, None)),
)
UNREADABLE_CLAIM = _log(
    "an unreadable claim",
    (1, Rec("lifecycle.started", {"handle": "local://n1/11", "t": 1.0}, None, None)),
)

# --- the verdict (§5, "The verdict") ---------------------------------------------

CLAIMED = [(1, started(None)), (2, commit(1, None, []))]
HELD = _log("a held claim", *CLAIMED)

EXITED = _log("a completed exit", *CLAIMED, (3, stopped(1, 2, [], completed=True)))
PREEMPTED_EXIT = _log("a preempted exit", *CLAIMED, (3, stopped(1, 2, [])))
ERRORED_EXIT = _log(
    "an errored exit", *CLAIMED, (3, stopped(1, 2, None, error="CUDA out of memory"))
)

KILLED = _log("a death report", *CLAIMED, (3, died(1, "killed", evidence="signal 9")))
EXIT_THEN_DEATH = _log(
    "an exit, then a death report",
    *CLAIMED,
    (3, stopped(1, 2, [])),
    (4, died(1, "errored", evidence="slurm: NODE_FAIL")),
)
DEATH_THEN_EXIT = _log(
    "a death report, then an exit",
    *CLAIMED,
    (3, died(1, "lost", evidence="probe: pid gone")),
    (4, stopped(1, 2, [], completed=True)),
)
SEVERAL_DEATHS = _log(
    "several death reports",
    *CLAIMED,
    (3, died(1, "killed", evidence="signal 9")),
    (4, died(1, "errored", evidence="slurm: NODE_FAIL")),
    (5, died(1, "lost", evidence="probe: pid gone")),
)
SEVERAL_DEATHS_REVERSED = _log(
    "several death reports, the other way round",
    *CLAIMED,
    (3, died(1, "lost", evidence="probe: pid gone")),
    (4, died(1, "errored", evidence="slurm: NODE_FAIL")),
    (5, died(1, "killed", evidence="signal 9")),
)
ONLY_LOST = _log(
    "only lost reports",
    *CLAIMED,
    (3, died(1, "lost", evidence="exit 0")),
    (4, died(1, "lost", evidence="probe: pid gone")),
)
LOST_BESIDE_PREEMPTED = _log(
    "a lost report beside a preempted one",
    *CLAIMED,
    (3, died(1, "lost", evidence="exit 0")),
    (4, died(1, "preempted", evidence="slurm: PREEMPTED")),
)

UNREADABLE_DEATH = _log(
    "an unreadable death report",
    *CLAIMED,
    (3, junk(died(1, "killed"), outcome="completed")),
)
ONLY_UNREADABLE = _log(
    "only unreadable records end the claim",
    *UNREADABLE_EXIT,
    (4, junk(died(1, "errored"), evidence="")),
)
A_REPORT_BESIDE_AN_UNREADABLE_ONE = _log(
    "a death report beside an unreadable one",
    *CLAIMED,
    (3, junk(died(1, "killed"), t=-1.0)),
    (4, died(1, "errored", evidence="slurm: NODE_FAIL")),
)
UNREADABLE_THEN_EXIT = _log(
    "an unreadable exit, then a readable one",
    *UNREADABLE_EXIT,
    (4, stopped(1, 2, [], completed=True)),
)
NAMELESS_IN_THE_WINDOW = _log(
    "records naming no claim in the claim's window",
    *CLAIMED,
    (3, nameless(stopped(1, 2, []))),
    (4, nameless(died(1, "killed"))),
    (5, stopped(1, 2, [], completed=True)),
)
NAMELESS_ONLY = _log(
    "a record naming no claim, alone in the window",
    *CLAIMED,
    (3, nameless(died(1, "killed"))),
)
A_DEAD_PASTS_JUNK = _log(
    "an older claim's unreadable exit, before the newest claim",
    *CLAIMED,
    (3, junk(stopped(1, 2, []), completed="yes")),
    (4, started(2)),
    (5, stopped(4, 2, [], completed=True)),
)
ANOTHER_CLAIMS_JUNK_IN_THE_WINDOW = _log(
    "an older claim's unreadable records, after the newest claim",
    *CLAIMED,
    (3, started(2)),
    (4, junk(stopped(1, 2, []), completed="yes")),
    (5, junk(died(1, "killed"), outcome="completed")),
    (6, stopped(3, 2, [], completed=True)),
)
AN_OLDER_CLAIMS_DEATH = _log(
    "an older claim's death report, after the newest claim",
    *CLAIMED,
    (3, started(2)),
    (4, died(1, "killed", evidence="signal 9")),
)

A_CLAIMS_LAUNCH_ENDED = _log(
    "a claim's launch ended",
    (1, launched("L1")),
    (2, started(None, launch="L1")),
    (3, commit(2, None, [])),
    (4, exited("L1", 0)),
)
UNCLAIMED = _log(
    "a launch that never claimed", (1, launched("L1")), (2, exited("L1", 1))
)
UNCLAIMED_CLEAN = _log(
    "a launch that exited 0 and never claimed",
    (1, launched("L1")),
    (2, exited("L1", 0)),
)
UNCLAIMED_KILLED = _log(
    "a launch killed before it claimed", (1, launched("L1")), (2, killed("L1", 9))
)
UNCLAIMED_TWICE = _log(
    "two launches that never claimed, reaped out of launch order",
    (1, launched("L1")),
    (2, launched("L2")),
    (3, killed("L2", 9)),
    (4, exited("L1", 1)),
)
BACKSTOP_UNCLAIMED = _log(
    "a backstop's terminated, which no launched names",
    (1, stop("pre")),
    (2, exited("backstop@2", 137)),
    (3, withdraw("pre")),
)
UNREADABLE_TERMINATED = _log(
    "an unreadable terminated, the newest",
    (1, launched("L1")),
    (2, exited("L1", 0)),
    (3, junk(exited("L2", 1), signal=9)),
)
TERMINATED_NAMING_NO_LAUNCH = _log(
    "a terminated naming no launch", (1, exited(None, 1))
)

# --- the stop fold (§5, "The answer fold") ------------------------------------------

STOPS = _log(
    "stops answered by name",
    (1, started(None)),
    (2, stop("honored")),
    (3, stop("withdrawn")),
    (4, stop("refused")),
    (5, stop("pending")),
    (6, withdraw("withdrawn")),
    (7, nak("refused")),
    (8, commit(1, None, [])),
    (9, stopped(1, 8, [], honored=["honored"])),
    (10, died(1, "killed", evidence="signal 9")),  # a death report spends nothing
)
RESENT = _log(
    "stops re-sent",
    (1, stop("again")),
    (2, stop("spent")),
    (3, withdraw("spent")),
    (4, stop("again")),
    (5, stop("spent")),  # a spent id stays spent
)
HONORED_BY_AN_UNREADABLE_EXIT = _log(
    "a stop an unreadable exit names",
    (1, started(None)),
    (2, stop("s")),
    (3, commit(1, None, [])),
    (4, junk(stopped(1, 3, [], honored=["s"]), completed="yes")),
)
HONORED_BY_A_DISPLACED_EXIT = _log(
    "a stop a displaced worker's exit honors",
    *CLAIMED,
    (3, started(2)),
    (4, stop("s")),
    (5, stopped(1, 2, [], honored=["s"])),
)
A_STOP_NAMING_NOTHING = _log(
    "a stop with no request_id", (1, stop(None)), (2, nak(None))
)

UNREADABLE = {
    "a malformed value body": {2},
    "a malformed value body, last": {2},
    "an unreadable exit": {3},
    "a commit naming no claim": {3},
    "a displaced worker's nameless commit": {5},
    "the claim's own nameless commit, then its next": {3},
    "another claim's unreadable commit": {5},
    "an unreadable claim": {1},
    "an unreadable death report": {3},
    "only unreadable records end the claim": {3, 4},
    "a death report beside an unreadable one": {3},
    "an unreadable exit, then a readable one": {3},
    "records naming no claim in the claim's window": {3, 4},
    "a record naming no claim, alone in the window": {3},
    "an older claim's unreadable exit, before the newest claim": {3},
    "an older claim's unreadable records, after the newest claim": {4, 5},
    "an unreadable terminated, the newest": {3},
    "a terminated naming no launch": {1},
    "a stop an unreadable exit names": {4},
    "a stop with no request_id": {1},
}


def test_every_log_is_valid_0_4_0():
    """Every row of every log conforms to 0.4.0, but for the rows a test makes
    unreadable on purpose."""
    pytest.importorskip("jsonschema")
    from test_schema_v0_4_0 import record_errors

    invalid = {}
    for label, rows in LOGS.items():
        ch = channel(rows)
        bad = {
            e.seq
            for e in ch.read()
            if record_errors(
                {
                    "seq": e.seq,
                    "topic": e.topic,
                    "name": e.name,
                    "request_id": e.request_id,
                    "body": e.body,
                }
            )
        }
        if bad:
            invalid[label] = bad
    assert invalid == UNREADABLE


# ----- the head ---------------------------------------------------------------------


def head(rows):
    return LatestClaimHead().resolve(channel(rows))


def test_an_empty_log_and_a_fresh_claim_hold_the_empty_lineage():
    assert head([]) == Head(None, False, 0)
    assert head(RESUMED[:1]) == Head(None, False, 1)


def test_the_head_follows_the_newest_claims_commits():
    assert head(RESUMED[:5]) == Head(5, False, 5)


def test_a_commitless_claims_head_is_its_resume_from_an_older_claims_node():
    assert head(COMMITLESS) == Head(3, False, 8)


def test_a_claims_readable_exit_is_its_head():
    assert head(COMMITLESS_ERRORED) == Head(9, False, 9)  # torn
    assert head(RESUMED[:7]) == Head(7, False, 7)  # preempted


def test_a_resume_from_the_previous_claims_exit_is_the_head_until_a_commit():
    assert head(RESUMED[:9]) == Head(7, False, 9)
    assert head(RESUMED[:10]) == Head(10, False, 10)


def test_a_trailing_rewind_moves_the_head_to_its_target():
    assert head(RESUMED[:11]) == Head(5, False, 11)
    assert head(TO_THE_ROOT) == Head(None, False, 4)
    assert head(FROM_THE_ROOT) == Head(6, False, 6)


def test_an_exits_parent_after_a_rewind_is_the_target():
    assert head(EXIT_AFTER_A_REWIND) == Head(5, False, 5)
    assert lineage(channel(EXIT_AFTER_A_REWIND), head=LatestClaimHead()) == [2, 5]


def test_a_displaced_workers_records_do_not_move_the_newest_claims_head():
    assert head(DISPLACED) == Head(3, False, 7)


def test_the_head_reads_nothing_above_its_w():
    ch = channel(RESUMED[:9])
    h = LatestClaimHead().resolve(ch)
    append(ch, RESUMED[9:])
    assert h == Head(7, False, 9)
    assert lineage(ch, head=h) == [3, 5, 7]


class _LateW(MemoryChannel):
    """A channel whose ``last_seq()`` was taken before the records above ``w``
    landed: what a resolve sees when a writer appends between its W and its
    reads."""

    def __init__(self, rows, w):
        super().__init__()
        append(self, rows)
        self.w = w

    def last_seq(self):
        return self.w


@pytest.mark.parametrize(
    "w, expected",
    [
        (7, Head(7, False, 7)),  # B's claim landed above W: A's exit is the head
        (10, Head(10, False, 10)),  # B's rewind landed above W
        (8, Head(7, False, 8)),  # B's claim is the last record at W
        (1, Head(None, False, 1)),  # so is A's, a fresh start
        (0, Head(None, False, 0)),
    ],
)
def test_a_record_landing_above_w_is_not_read(w, expected):
    ch = _LateW(RESUMED, w)
    assert LatestClaimHead().resolve(ch) == expected
    if expected.node is not None:
        assert AtNode(expected.node).resolve(ch) == Head(expected.node, True, w)
    with pytest.raises(ValueError):
        AtNode(14).resolve(ch)


def test_a_head_resolves_to_itself_so_a_poll_reads_one_head():
    ch = channel(RESUMED[:10])
    h = LatestClaimHead().resolve(ch)
    assert h.resolve(ch) is h
    append(ch, RESUMED[10:])
    assert lineage(ch, head=h) == [3, 5, 7, 10]
    assert lineage(ch, head=LatestClaimHead()) == [3, 5, 13, 14]


def test_a_head_holds_its_invariants():
    with pytest.raises(ValueError):
        Head(None, True, 3)  # the empty lineage is not settled
    with pytest.raises(ValueError):
        Head(4, False, 3)  # nothing above w
    with pytest.raises(ValueError):
        Head(0, True, 3)  # no seq 0


# ----- settledness ------------------------------------------------------------------


def test_a_completed_exit_closes_its_lineage():
    assert head(RESUMED).settled
    assert head(UNCOMMITTED) == Head(8, True, 9)


def test_a_preempted_or_errored_exit_does_not():
    assert not head(RESUMED[:7]).settled
    assert not head(COMMITLESS_ERRORED).settled


def test_a_displaced_workers_completed_exit_does_not_settle_the_newest_claims_read():
    assert not head(DISPLACED).settled
    assert AtNode(7).resolve(channel(DISPLACED)) == Head(7, True, 7)
    assert lineage(channel(DISPLACED), head=AtNode(7)) == [3, 6, 7]


def test_at_node_is_settled_and_permanent():
    ch = channel(RESUMED[:10])
    before = AtNode(10).resolve(ch)
    reads = (lineage(ch, head=before), series(ch, "loss", head=before))
    append(ch, RESUMED[10:])
    after = AtNode(10).resolve(ch)
    assert (before, after) == (Head(10, True, 10), Head(10, True, 14))
    assert (lineage(ch, head=after), series(ch, "loss", head=after)) == reads


def test_at_node_reads_any_node_an_abandoned_branch_a_torn_exit_and_an_exit():
    ch = channel(RESUMED)
    assert lineage(ch, head=AtNode(10)) == [3, 5, 7, 10]
    assert lineage(ch, head=AtNode(7)) == [3, 5, 7]
    assert lineage(channel(COMMITLESS_ERRORED), head=AtNode(5)) == [3, 5]


@pytest.mark.parametrize("seq", [0, 2, 1, 8, 11, 15, -1])
def test_at_node_raises_on_a_seq_that_is_not_a_node_or_is_above_w(seq):
    with pytest.raises(ValueError):
        AtNode(seq).resolve(channel(RESUMED))


def test_at_node_raises_on_a_heartbeat():
    with pytest.raises(ValueError):
        AtNode(8).resolve(channel(COMMITLESS))


def test_a_hand_built_head_that_is_no_node_raises():
    with pytest.raises(ValueError):
        lineage(channel(RESUMED), head=Head(2, False, 14))


# ----- nodes and commits ------------------------------------------------------------


def test_commits_count_iterations_across_a_resume_and_an_extended_exit():
    """The exit B resumed from is on its lineage and adds no iteration; its
    value is still an element."""
    ch = channel(RESUMED)
    branch = lineage(ch, head=AtNode(10))
    assert [count(ch, Commits(), node=n) for n in branch] == [1, 2, 2, 3]
    assert [count(ch, "loss", node=n) for n in branch] == [1, 2, 3, 4]
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 3
    assert prefix_len(ch, "loss", head=LatestClaimHead()) == 3


def test_a_count_at_an_exit_takes_its_values_and_no_iteration():
    ch = channel(RESUMED)
    assert count(ch, Commits(), node=7) == count(ch, Commits(), node=5) == 2
    assert count(ch, "loss", node=7) == count(ch, "loss", node=5) + 1
    assert count(ch, "loss", node=14) == count(ch, "loss", node=13) == 3


def test_resume_and_rewind_form_the_parent_chains():
    ch = channel(RESUMED)
    assert lineage(ch, head=LatestClaimHead()) == [3, 5, 13, 14]
    assert series(ch, "loss", head=LatestClaimHead()) == [
        Element(3, 2, 0.9, 1.5),
        Element(5, 4, 0.8, 1.5),
        Element(13, 12, 0.5, 1.5),
    ]


def test_a_value_no_node_names_is_excluded():
    ch = channel(UNCOMMITTED)
    assert [e.seq for e in series(ch, "loss", head=LatestClaimHead())] == [2, 6]
    assert series(ch, "status", head=LatestClaimHead()) == []
    assert count(ch, "status", node=8) == 0
    ch = channel(COMMITLESS_ERRORED)
    assert [e.seq for e in series(ch, "loss", head=LatestClaimHead())] == [2]
    assert prefix_len(ch, "status", head=LatestClaimHead()) == 0


def test_a_value_committed_on_two_lineages_is_one_element_of_each():
    ch = channel(TWO_BRANCHES)
    assert series(ch, "loss", head=LatestClaimHead()) == [Element(5, 2, 0.9, 1.5)]
    assert series(ch, "loss", head=AtNode(3)) == [Element(3, 2, 0.9, 1.5)]


def test_order_within_a_node_follows_values():
    ch = channel(IN_LINEAGE_ORDER)
    assert [e.seq for e in series(ch, "loss", head=AtNode(5))] == [4, 2]
    assert [e.seq for e in series(ch, "loss", head=LatestClaimHead())] == [4, 2, 3]


def test_last_element_is_the_last_in_lineage_order_not_the_newest_by_seq():
    ch = channel(IN_LINEAGE_ORDER)
    assert last_element(ch, "loss", head=LatestClaimHead()) == Element(6, 3, 0.8, 1.5)
    assert last_element(ch, "loss", head=AtNode(5)) == Element(5, 2, 0.9, 1.5)


def test_the_empty_lineage_counts_0_and_has_no_last_element():
    ch = channel(TO_THE_ROOT)
    assert lineage(ch, head=LatestClaimHead()) == []
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 0
    assert prefix_len(ch, "loss", head=LatestClaimHead()) == 0
    assert series(ch, "loss", head=LatestClaimHead()) == []
    assert last_element(ch, "loss", head=LatestClaimHead()) is None
    assert count(ch, "loss", node=None) == count(ch, Commits(), node=None) == 0


def test_counts_and_indices_are_len_and_range_of_one_convention():
    ch = channel(RESUMED)
    for h in (AtNode(10), LatestClaimHead()):
        for n in lineage(ch, head=h):
            elements = series(ch, "loss", head=AtNode(n))
            assert count(ch, "loss", node=n) == len(elements)
            assert prefix_len(ch, "loss", head=AtNode(n)) == len(elements)
            assert last_element(ch, "loss", head=AtNode(n)) == elements[-1]


def test_membership_is_by_the_committing_claims_declaration():
    ch = channel(KIND_CHANGED)
    assert series(ch, "lr", head=LatestClaimHead()) == [Element(9, 8, 0.05, 1.5)]
    assert count(ch, "lr", node=9) == 1
    assert count(ch, "loss", node=9) == 1


def test_a_name_that_was_a_source_throughout_counts_0():
    ch = channel(KIND_CHANGED)
    assert count(ch, "lr", node=5) == 0
    assert series(ch, "lr", head=AtNode(6)) == []
    assert last_element(ch, "lr", head=AtNode(6)) is None


# ----- well-formedness, each at its stated check -------------------------------------


@pytest.mark.parametrize(
    "rows, at, bad",
    [
        (LATER_VALUE, AtNode(4), 2),
        (LATER_VALUE, LatestClaimHead(), 2),
        (LATER_PARENT, AtNode(5), 3),
        (PARENT_A_VALUE, AtNode(3), 3),
        (PARENT_CLOSED, AtNode(5), 5),
        (PARENT_TORN, AtNode(5), 5),
        (TWICE, LatestClaimHead(), 4),
    ],
)
def test_a_walk_checks_every_node_it_walks(rows, at, bad):
    """A parent or a values seq at or above its node's own, a parent that is
    not an extendable node, and a seq committed twice along one lineage."""
    ch = channel(rows)
    for read in (
        lambda: lineage(ch, head=at),
        lambda: prefix_len(ch, Commits(), head=at),
        lambda: series(ch, "loss", head=at),
    ):
        with pytest.raises(MalformedRecordError) as exc:
            read()
        assert exc.value.seq == bad


@pytest.mark.parametrize(
    "rows, bad",
    [
        (RESUME_LATER, 3),
        (REWIND_LATER, 3),
        (RESUME_CLOSED, 4),
        (RESUME_TORN, 4),
        (RESUME_A_HEARTBEAT, 3),
        (REWIND_TO_A_VALUE, 3),
        (ROLLBACK, 4),
        (EXIT_BEFORE_THE_REWIND, 5),
        (PARENT_CLOSED, 5),
    ],
)
def test_the_head_fold_checks_the_newest_claims_records(rows, bad):
    """A resume_from at or above its own seq or naming no extendable node, and
    a node whose parent is not its claim's position before it."""
    with pytest.raises(MalformedRecordError) as exc:
        head(rows)
    assert exc.value.seq == bad


def test_a_parent_that_is_not_its_claims_position_is_checked_by_the_head_fold_only():
    """It stays within one writer's order: a walk across writers cannot see it."""
    assert lineage(channel(ROLLBACK), head=AtNode(4)) == [2, 4]
    assert lineage(channel(EXIT_BEFORE_THE_REWIND), head=AtNode(5)) == [2, 3, 5]


def test_a_seq_committed_twice_is_the_walks_check():
    ch = channel(TWICE)
    assert head(TWICE) == Head(4, False, 4)
    assert lineage(ch, head=AtNode(3)) == [3]


def test_a_values_seq_naming_a_non_value_raises_only_when_resolved():
    ch = channel(NAMES_A_HEARTBEAT)
    assert lineage(ch, head=LatestClaimHead()) == [3]
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 1
    for read in (
        lambda: count(ch, "loss", node=3),
        lambda: series(ch, "loss", head=LatestClaimHead()),
        lambda: last_element(ch, "loss", head=LatestClaimHead()),
    ):
        with pytest.raises(MalformedRecordError) as exc:
            read()
        assert exc.value.seq == 3


def test_a_malformed_value_body_raises_on_read_never_on_count():
    ch = channel(BAD_BODY)
    assert prefix_len(ch, "loss", head=LatestClaimHead()) == 2
    assert last_element(ch, "loss", head=LatestClaimHead()) == Element(4, 3, 0.8, 1.5)
    with pytest.raises(MalformedRecordError) as exc:
        series(ch, "loss", head=LatestClaimHead())
    assert exc.value.seq == 2
    ch = channel(BAD_BODY_LAST)
    assert count(ch, "loss", node=4) == 2
    with pytest.raises(MalformedRecordError) as exc:
        last_element(ch, "loss", head=LatestClaimHead())
    assert exc.value.seq == 2


@pytest.mark.parametrize(
    "rows, at", [(CLAIM_LATER, LatestClaimHead()), (CLAIM_NOT_A_CLAIM, AtNode(3))]
)
def test_membership_resolves_the_committing_claim_which_must_be_a_claim_below_it(
    rows, at
):
    ch = channel(rows)
    assert prefix_len(ch, Commits(), head=at) == len(lineage(ch, head=at))
    with pytest.raises(MalformedRecordError) as exc:
        series(ch, "loss", head=at)
    assert exc.value.seq == 3


# ----- readable means the schema's ---------------------------------------------------

_DEFINITIONS = {
    "lifecycle.started": ("lifecycle-v0.6", "Started", _Started),
    "lifecycle.commit": ("lifecycle-v0.6", "Commit", _Commit),
    "lifecycle.rewind": ("lifecycle-v0.6", "Rewind", _Rewind),
    "lifecycle.stopped": ("lifecycle-v0.6", "Stopped", _Stopped),
    "supervisor.died": ("supervisor-v0.5", "Died", _Died),
    "supervisor.terminated": ("supervisor-v0.5", "Terminated", _Terminated),
    "value": ("value-v0.3", "Value", _Value),
}


def _edges():
    """Bodies one field off a valid one, at the edges the parse mirrors, and
    with a key added or the first removed."""
    off = {
        "lifecycle.started": [
            ("handle", 5),
            ("t", -1.0),
            ("t", True),
            ("resume_from", True),
            ("resume_from", 0),
            ("sources", "lr"),
            ("sources", ["lr", "lr"]),
        ],
        "lifecycle.commit": [
            ("claim_seq", True),
            ("claim_seq", None),
            ("consumed_seq", -1),
            ("consumed_seq", True),
            ("parent", 0),
            ("values", "x"),
            ("values", [True]),
            ("fired", ["q", "q"]),
            ("expired", [""]),
            ("t", "1"),
        ],
        "lifecycle.rewind": [
            ("claim_seq", None),
            ("resume_from", -3),
            ("t", None),
        ],
        "lifecycle.stopped": [
            ("completed", "yes"),
            ("error", 5),
            ("claim_seq", True),
            ("honored", ["s", "s"]),
            ("parent", 0),
            ("values", [2, 2]),
            ("values", None),
            ("fired", [""]),
            ("t", -1.0),
        ],
        "supervisor.died": [
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
        "supervisor.terminated": [
            ("reason", "crashed"),
            ("reason", None),
            ("exit_code", -1),
            ("exit_code", None),
            ("exit_code", True),
            ("signal", 9),
            ("t", -1.0),
        ],
        "value": [("t", -1.0), ("t", "x"), ("t", True), ("t", None)],
    }
    bodies = {
        "lifecycle.started": started(None, sources=["lr"]).body,
        "lifecycle.commit": commit(1, 2, [3], fired=["q"]).body,
        "lifecycle.rewind": rewind(1, 2).body,
        "lifecycle.stopped": stopped(1, 2, [3]).body,
        "supervisor.died": died(1, "killed").body,
        "supervisor.terminated": exited("L", 0).body,
        "value": value("loss", 0.9).body,
    }
    for topic, changes in off.items():
        for field, v in changes:
            yield topic, {**bodies[topic], field: v}
        yield topic, {**bodies[topic], "extra": 1}
        yield topic, {k: v for k, v in list(bodies[topic].items())[1:]}
    torn = {**bodies["lifecycle.stopped"], "values": None}
    yield "lifecycle.stopped", {**torn, "completed": True}  # closed, not torn
    yield "lifecycle.stopped", {**torn, "error": "boom"}
    yield "lifecycle.stopped", {**torn, "completed": True, "error": "boom"}
    yield "lifecycle.stopped", {**torn, "expired": ["q"]}
    by_signal = killed("L", 9).body
    yield "supervisor.terminated", by_signal
    yield "supervisor.terminated", {**by_signal, "signal": 0}
    yield "supervisor.terminated", {**by_signal, "signal": None}
    yield "supervisor.terminated", {**by_signal, "signal": True}
    yield "supervisor.terminated", {**by_signal, "exit_code": 0}


def _bodies():
    from test_schema_v0_4_0 import NEGATIVE, VALID

    for r in [*VALID.values(), *NEGATIVE.values()]:
        if r["topic"] in _DEFINITIONS:
            yield r["topic"], r["body"]
    yield from _edges()


def test_a_body_is_readable_here_if_and_only_if_its_schema_definition_admits_it():
    """``MalformedRecordError`` and "absent for the head" rest on one meaning of
    readable: the 0.4.0 schema's, body by body."""
    pytest.importorskip("jsonschema")
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator

    protocol = Path(__file__).resolve().parent.parent / "protocol"
    disagree = []
    for topic, body in _bodies():
        schema, definition, cls = _DEFINITIONS[topic]
        defs = json.loads((protocol / f"{schema}.schema.json").read_text())["$defs"]
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
    assert disagree == []


def test_a_whole_number_float_is_no_seq_here_though_json_schema_admits_it():
    """JSON Schema's ``integer`` admits 3.0; these reads, like
    ``vocabulary.payloads`` and the migration step, refuse it. No writer of a
    0.4.0 log writes one: Python's ``json`` writes an int as an int."""
    with pytest.raises(ValueError):
        _Commit(**{**commit(1, 2, [3]).body, "parent": 2.0})


# ----- the head fold's decisions -----------------------------------------------------


def test_an_unreadable_exit_is_absent_for_the_head():
    assert head(UNREADABLE_EXIT) == Head(2, False, 3)
    with pytest.raises(MalformedRecordError) as exc:
        AtNode(3).resolve(channel(UNREADABLE_EXIT))
    assert exc.value.seq == 3


@pytest.mark.parametrize(
    "rows, bad",
    [
        (TWO_EXITS, 4),
        (AFTER_THE_EXIT, 4),
        (UNREADABLE_CLAIM, 1),
    ],
)
def test_the_head_fold_refuses_to_guess(rows, bad):
    """The exit is its claim's last node, and there is at most one per claim; an
    unreadable claim states no resume_from."""
    with pytest.raises(MalformedRecordError) as exc:
        head(rows)
    assert exc.value.seq == bad


def test_another_claims_unreadable_record_does_not_poison_the_newest_claim():
    assert head(ANOTHER_CLAIMS_JUNK) == Head(3, False, 5)


def test_a_record_naming_no_claim_is_skipped_as_no_claims():
    """Raising on it would let a displaced worker's junk in the window make every
    read of the newest claim raise, until a newer claim."""
    assert head(NAMES_NO_CLAIM) == Head(2, False, 3)
    assert head(DISPLACED_NAMELESS) == Head(6, False, 6)
    assert lineage(channel(DISPLACED_NAMELESS), head=LatestClaimHead()) == [2, 3, 6]


def test_the_claims_own_nameless_commit_is_caught_by_its_next_nodes_parent():
    with pytest.raises(MalformedRecordError) as exc:
        head(OWN_NAMELESS)
    assert exc.value.seq == 4
    assert "not its claim's position 2" in exc.value.detail


# ----- the verdict (§5, "The verdict") ----------------------------------------------

C, P, E, K, L = (
    Outcome04.COMPLETED,
    Outcome04.PREEMPTED,
    Outcome04.ERRORED,
    Outcome04.KILLED,
    Outcome04.LOST,
)


def by_exit(node, claim, outcome, *, error=None, malformed=()):
    return RunResult04(
        outcomes=frozenset({outcome}),
        reports=(),
        node=node,
        claim_seq=claim,
        malformed=malformed,
        tier=Tier.EXIT,
        error=error,
    )


def by_deaths(claim, outcomes, reports, *, malformed=()):
    return RunResult04(
        outcomes=frozenset(outcomes),
        reports=tuple(reports),
        node=None,
        claim_seq=claim,
        malformed=malformed,
        tier=Tier.DIED,
        error=None,
    )


def by_launch(outcome):
    return RunResult04(
        outcomes=frozenset({outcome}),
        reports=(),
        node=None,
        claim_seq=None,
        malformed=(),
        tier=Tier.TERMINATED,
        error=None,
    )


def verdict_of(rows):
    return verdict(channel(rows))


def test_an_empty_log_and_a_held_claim_have_no_verdict():
    assert verdict_of([]) is None
    assert verdict_of(HELD) is None


@pytest.mark.parametrize(
    "rows, expected",
    [
        (EXITED, by_exit(3, 1, C)),
        (PREEMPTED_EXIT, by_exit(3, 1, P)),
        (ERRORED_EXIT, by_exit(3, 1, E, error="CUDA out of memory")),
    ],
)
def test_the_claims_exit_is_its_self_report(rows, expected):
    assert verdict_of(rows) == expected


def test_a_died_naming_the_claim_ends_it():
    assert verdict_of(KILLED) == by_deaths(1, {K}, [DeathReport(3, K, "signal 9", 5.0)])


@pytest.mark.parametrize(
    "rows, expected",
    [(EXIT_THEN_DEATH, by_exit(3, 1, P)), (DEATH_THEN_EXIT, by_exit(4, 1, C))],
)
def test_the_self_report_outranks_the_death_reports_whatever_their_order(
    rows, expected
):
    """Its ``reports`` are empty: they are the death reports' viewpoint's."""
    assert verdict_of(rows) == expected


def test_several_death_reports_are_kept_as_a_set_whatever_their_order():
    reports = [
        DeathReport(3, K, "signal 9", 5.0),
        DeathReport(4, E, "slurm: NODE_FAIL", 5.0),
        DeathReport(5, L, "probe: pid gone", 5.0),
    ]
    assert verdict_of(SEVERAL_DEATHS) == by_deaths(1, {K, E}, reports)
    other = verdict_of(SEVERAL_DEATHS_REVERSED)
    assert other.outcomes == {K, E}
    assert [(r.seq, r.outcome) for r in other.reports] == [(3, L), (4, E), (5, K)]


def test_lost_is_dropped_beside_another_outcome_and_kept_alone():
    assert verdict_of(LOST_BESIDE_PREEMPTED) == by_deaths(
        1,
        {P},
        [
            DeathReport(3, L, "exit 0", 5.0),
            DeathReport(4, P, "slurm: PREEMPTED", 5.0),
        ],
    )
    assert verdict_of(ONLY_LOST) == by_deaths(
        1,
        {L},
        [DeathReport(3, L, "exit 0", 5.0), DeathReport(4, L, "probe: pid gone", 5.0)],
    )


@pytest.mark.parametrize(
    "rows, expected",
    [
        (UNREADABLE_EXIT, by_deaths(1, {L}, [], malformed=(3,))),
        (UNREADABLE_DEATH, by_deaths(1, {L}, [], malformed=(3,))),
        (ONLY_UNREADABLE, by_deaths(1, {L}, [], malformed=(3, 4))),
    ],
)
def test_an_unreadable_record_ends_its_claim_as_a_lost_named_in_malformed(
    rows, expected
):
    """It says only that its claim ended, which is all ``lost`` says, and no
    more of its body is read."""
    assert verdict_of(rows) == expected


def test_an_unreadable_report_is_skipped_and_its_lost_dropped_beside_another():
    assert verdict_of(A_REPORT_BESIDE_AN_UNREADABLE_ONE) == by_deaths(
        1, {E}, [DeathReport(4, E, "slurm: NODE_FAIL", 5.0)], malformed=(3,)
    )


def test_an_unreadable_exit_is_no_self_report():
    assert verdict_of(UNREADABLE_THEN_EXIT) == by_exit(4, 1, C, malformed=(3,))


def test_a_record_naming_no_claim_ends_nothing_and_is_named_in_malformed():
    """It may have been the claim's own: the verdict reports it, as 0.3.0's
    raised on it, but cannot let it end the claim."""
    assert verdict_of(NAMELESS_IN_THE_WINDOW) == by_exit(5, 1, C, malformed=(3, 4))
    assert verdict_of(NAMELESS_ONLY) is None  # the claim is held


def test_the_verdict_reads_only_the_newest_claims_window():
    """The claim is a Markov boundary: an older claim's records, readable or
    not, before it or after it, say nothing of the newest claim's end."""
    assert verdict_of(A_DEAD_PASTS_JUNK) == by_exit(5, 4, C)
    assert verdict_of(ANOTHER_CLAIMS_JUNK_IN_THE_WINDOW) == by_exit(6, 3, C)
    assert verdict_of(AN_OLDER_CLAIMS_DEATH) is None
    assert verdict_of(DISPLACED) is None  # A's completed exit, after B's claim


def test_two_readable_exits_of_one_claim_refuse_to_guess():
    """At most one per claim, by the dying-breath CAS (§2): as the head fold."""
    with pytest.raises(MalformedRecordError) as exc:
        verdict_of(TWO_EXITS)
    assert exc.value.seq == 4


def test_terminated_ends_nothing_for_a_claimed_run():
    assert verdict_of(A_CLAIMS_LAUNCH_ENDED) is None


@pytest.mark.parametrize(
    "rows, outcome",
    [(UNCLAIMED, E), (UNCLAIMED_CLEAN, C), (UNCLAIMED_KILLED, K)],
)
def test_terminated_stays_the_verdict_of_an_unclaimed_run(rows, outcome):
    """As today's null-worker startup crash: a signal is killed, an exit 0
    completed, and any other exit errored."""
    assert verdict_of(rows) == by_launch(outcome)


def test_an_unclaimed_run_takes_the_newest_terminated():
    """§5's "as today's": the newest ``terminated``, here not the newest
    launch's."""
    assert verdict_of(UNCLAIMED_TWICE) == by_launch(E)


def test_a_backstops_terminated_gives_the_verdict_though_no_launched_names_it():
    assert verdict_of(BACKSTOP_UNCLAIMED) == by_launch(E)


@pytest.mark.parametrize(
    "rows, bad", [(UNREADABLE_TERMINATED, 3), (TERMINATED_NAMING_NO_LAUNCH, 1)]
)
def test_an_unclaimed_runs_unreadable_terminated_raises(rows, bad):
    """As today's: the unclaimed verdict rests on that one record."""
    with pytest.raises(MalformedRecordError) as exc:
        verdict_of(rows)
    assert exc.value.seq == bad


@pytest.mark.parametrize(
    "rows, w, expected",
    [
        (KILLED, 2, None),  # the death report landed above W
        (EXIT_THEN_DEATH, 3, by_exit(3, 1, P)),
        (
            SEVERAL_DEATHS,
            4,
            by_deaths(
                1,
                {K, E},
                [
                    DeathReport(3, K, "signal 9", 5.0),
                    DeathReport(4, E, "slurm: NODE_FAIL", 5.0),
                ],
            ),
        ),
        # the newest claim landed above W: the older one's unreadable exit ends it
        (A_DEAD_PASTS_JUNK, 3, by_deaths(1, {L}, [], malformed=(3,))),
        (UNCLAIMED_TWICE, 3, by_launch(K)),  # L1's reap landed above W
        (UNCLAIMED_TWICE, 2, None),
        (UNCLAIMED_TWICE, 0, None),
    ],
)
def test_the_verdict_reads_nothing_above_its_w(rows, w, expected):
    assert verdict(_LateW(rows, w)) == expected


@pytest.mark.parametrize(
    "fields",
    [
        {"outcomes": frozenset()},  # never empty
        {"node": None},  # an exit's node
        {"claim_seq": None},  # an exit's claim
        {"reports": (DeathReport(4, K, "signal 9", 5.0),)},  # only under died
    ],
)
def test_an_exits_run_result_holds_its_viewpoints_shape(fields):
    with pytest.raises(ValueError):
        RunResult04(**{**by_exit(3, 1, P).__dict__, **fields})


@pytest.mark.parametrize(
    "fields",
    [
        {"node": 3},  # only under exit
        {"error": "boom"},  # only under exit
    ],
)
def test_a_death_reports_run_result_holds_its_viewpoints_shape(fields):
    with pytest.raises(ValueError):
        RunResult04(**{**by_deaths(1, {K}, []).__dict__, **fields})


def test_a_launchs_run_result_names_no_claim():
    with pytest.raises(ValueError):
        RunResult04(**{**by_launch(K).__dict__, "claim_seq": 1})


# ----- the stop fold (§5, "The answer fold") ----------------------------------------


def test_a_stop_is_spent_by_an_exits_honored_a_withdrawal_or_a_nak():
    """Not by a death report."""
    assert undischarged_stops(channel(STOPS)) == [5]


def test_a_stop_is_spent_by_name_and_a_spent_id_stays_spent():
    """A re-sent live id is one stop, at its newest send."""
    assert undischarged_stops(channel(RESENT)) == [4]


def test_an_unreadable_exits_honored_is_not_read():
    """No fold reads more of an unreadable record's body (§5)."""
    assert undischarged_stops(channel(HONORED_BY_AN_UNREADABLE_EXIT)) == [2]


def test_a_stop_honored_on_one_branch_is_not_rearmed_on_another():
    """A stop is a request about the process: a displaced worker's exit that
    honors it spends it (§2)."""
    assert undischarged_stops(channel(HONORED_BY_A_DISPLACED_EXIT)) == []


def test_a_stop_naming_nothing_is_never_pending():
    assert undischarged_stops(channel(A_STOP_NAMING_NOTHING)) == []


@pytest.mark.parametrize(
    "w, expected",
    [(0, []), (5, [2, 3, 4, 5]), (6, [2, 4, 5]), (8, [2, 5]), (10, [5])],
)
def test_the_stop_fold_reads_nothing_above_its_w(w, expected):
    assert undischarged_stops(_LateW(STOPS, w)) == expected


# ----- the migration's goldens, read as the corpus runner will -----------------------


def _golden(rid):
    from tests.lineage_logs import OUTPUT

    ch = MemoryChannel()
    for seq, topic, name, request_id, body in OUTPUT[rid]:
        assert ch.send(body, topic=topic, name=name, request_id=request_id) == seq
    return ch


def _golden_ids():
    from tests.lineage_logs import OUTPUT

    return list(OUTPUT)


@pytest.mark.parametrize("rid", _golden_ids())
def test_every_goldens_head_lineage_and_series_read(rid):
    """At the newest claim's head and at every node: the lineage, the
    ``Commits()`` count and every name's series and count read, consistently."""
    ch = _golden(rid)
    names = {e.name for e in ch.read(topics=["value"])}
    commits = {e.seq for e in ch.read(topics=["lifecycle.commit"])}
    nodes = commits | {e.seq for e in ch.read(topics=["lifecycle.stopped"])}
    heads = [LatestClaimHead().resolve(ch), *(AtNode(n) for n in sorted(nodes))]
    for h in heads:
        chain = lineage(ch, head=h)
        n = h.resolve(ch).node
        assert chain == ([] if n is None else [*lineage(ch, head=AtNode(n))])
        assert prefix_len(ch, Commits(), head=h) == len(commits.intersection(chain))
        if n is not None:
            assert chain[-1] == n
            assert count(ch, Commits(), node=n) == prefix_len(ch, Commits(), head=h)
        for name in names:
            elements = series(ch, name, head=h)
            assert [e.node for e in elements] == sorted(
                (e.node for e in elements), key=chain.index
            )
            assert prefix_len(ch, name, head=h) == len(elements)
            assert count(ch, name, node=n) == len(elements)


def test_the_displaced_golden_reads_the_newest_claims_lineage():
    ch = _golden("displaced")
    assert LatestClaimHead().resolve(ch) == Head(12, True, 12)
    assert lineage(ch, head=LatestClaimHead()) == [3, 4, 8, 10, 12]
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 4
    assert series(ch, "loss", head=LatestClaimHead()) == [
        Element(3, 2, 0.9, 2.0),
        Element(8, 6, 0.8, 6.0),
        Element(10, 9, 0.7, 8.0),
    ]
    assert lineage(ch, head=AtNode(11)) == [3, 4, 7, 11]  # the displaced exit
    assert count(ch, Commits(), node=11) == 3


def test_the_loading_crash_golden_reads_past_the_torn_retry():
    ch = _golden("resumed_past_a_loading_crash")
    assert LatestClaimHead().resolve(ch) == Head(15, True, 15)
    assert lineage(ch, head=LatestClaimHead()) == [3, 5, 7, 14, 15]
    assert [e.value for e in series(ch, "loss", head=LatestClaimHead())] == [
        0.9,
        0.8,
        0.7,
        0.6,
    ]
    assert series(ch, "status", head=LatestClaimHead()) == []  # the retry's loading
    assert lineage(ch, head=AtNode(11)) == [3, 5, 7, 11]  # the retry's torn exit
    assert count(ch, Commits(), node=11) == 3


def _golden_rows(rid):
    from tests.lineage_logs import OUTPUT

    return [
        (seq, Rec(topic, body, name, request_id))
        for seq, topic, name, request_id, body in OUTPUT[rid]
    ]


def _golden_verdicts():
    """Every golden's verdict, by hand from its rows: the newest claim's
    readable exit, else the death reports naming it; none of them leaves an
    unclaimed run with a ``terminated``."""
    from tests.lineage_logs import BACKSTOP, MIGRATED, SACCT

    def backstopped(seq, claim, t):
        return by_deaths(claim, {E}, [DeathReport(seq, E, BACKSTOP, t)])

    def released(seq, claim, t):
        return by_deaths(claim, {P}, [DeathReport(seq, P, MIGRATED, t)])

    return {
        "displaced": by_exit(12, 5, C),
        "exit": by_exit(4, 1, P),
        "backstopped": backstopped(3, 1, 3.0),
        "late_backstop": by_exit(3, 1, P),
        "reclaimed": by_deaths(
            1, {P}, [DeathReport(4, P, "reclaimed: slurm TIMEOUT", 1001.0)]
        ),
        "claimless": None,
        "loading": by_exit(12, 8, C),
        "skipped": by_exit(14, 1, C),
        "post_loop": by_exit(8, 1, C),
        "trailing": by_exit(8, 1, C),
        "preempted": by_exit(6, 1, P),
        "errored": by_exit(5, 1, E, error="CUDA out of memory"),
        "resumed_past_a_loading_crash": by_exit(15, 12, C),
        "resumed_beside_a_real_beat": by_exit(15, 12, C),
        "after_stopped": by_exit(4, 1, C),
        "above": by_exit(8, 4, C),
        "loading_retry_released": by_exit(11, 8, C),
        "loading_retry_unended": by_exit(11, 8, C),
        "loading_retry_at_its_resume_step": by_exit(15, 12, C),
        "beatless_full_step": by_exit(12, 9, C),
        "displacement_parent": by_exit(16, 13, C),
        "commitless": by_exit(13, 11, C),
        "resumed_past_an_unrecorded_step": by_exit(12, 7, C),
        "resumed_past_a_preempted_exit": by_exit(11, 8, C),
        "loading_retry_beside_a_displaced_worker": by_exit(5, 4, E, error="OSError"),
        "resumed_from_a_step_committed_after_the_claim": by_exit(7, 4, C),
        "resumed_on_an_older_claims_step": by_exit(11, 8, C),
        "doubly_displaced": None,
        "resumed_from_a_tick_skipped_after_the_claim": by_exit(8, 3, C),
        "resumed_from_a_tick_skipped_across_the_claim": by_exit(8, 5, C),
        "watermarked": by_exit(8, 1, P),
        "later_error": by_exit(3, 1, P),
        "later_default": by_exit(4, 1, P),
        "later_release": by_exit(4, 1, P),
        "later_error_honored": by_exit(4, 1, P),
        "claimless_error": None,
        "claimless_release": None,
        "claimless_backstop": None,  # the claim at 4 is held
        "second_backstop": backstopped(3, 1, 3.0),
        "backstop_id_taken": by_exit(4, 1, P),
        "launched": by_exit(4, 2, C),
        "attributed": by_deaths(2, {K}, [DeathReport(5, K, "signal 9", 4.0)]),
        "wrapper": None,  # a wrapper's terminated ends nothing
        "another_host": None,
        "late_reap": by_exit(7, 5, C),
        "two_claims_one_launch": by_deaths(4, {L}, [DeathReport(7, L, "exit 0", 5.0)]),
        "after_an_attributed_death": None,
        "reclaim_reported_later": released(5, 1, 5.0),
        "reclaim_of_10_23": by_deaths(1, {E}, [DeathReport(5, E, SACCT, 5.0)]),
        "beaten_after": released(3, 1, 3.0),  # the beat after it ends nothing
        "reported_later_boundary": released(6, 4, 6.0),
        "cooperative": by_exit(8, 1, P),
    }


def test_every_golden_has_a_verdict_by_hand():
    assert set(_golden_verdicts()) == set(_golden_ids())


@pytest.mark.parametrize("rid", _golden_ids())
def test_every_goldens_verdict(rid):
    assert verdict(_golden(rid)) == _golden_verdicts()[rid]


def test_review_focus_4_a_backstop_then_a_default_body_reads_errored():
    """One death, ``{errored}``: the default body after the backstop wrote no
    second report and no exit."""
    assert verdict(_golden("backstopped")).outcomes == {E}


def test_a_claimless_backstops_terminated_is_the_verdict_until_a_claim():
    """Owner ruling c′: a never-claimed run keeps its verdict through the
    backstop's ``terminated``, under a minted launch id no ``launched`` names."""
    rows = _golden_rows("claimless_backstop")
    assert verdict(_LateW(rows, 3)) == by_launch(K)
    assert verdict(_LateW(rows, 4)) is None


@pytest.mark.parametrize("rid", _golden_ids())
def test_every_goldens_stops_are_spent(rid):
    assert undischarged_stops(_golden(rid)) == []


def test_a_goldens_stop_is_pending_until_its_answer():
    assert undischarged_stops(_LateW(_golden_rows("exit"), 3)) == [3]
    assert undischarged_stops(_LateW(_golden_rows("backstopped"), 4)) == [4]


@pytest.mark.parametrize("rid", _golden_ids())
def test_every_goldens_verdict_and_stops_read_at_every_w(rid):
    """At every prefix: no raise, the verdict is about the newest claim at or
    below W, and a pending stop is a stop at or below W."""
    rows = _golden_rows(rid)
    for w in range(len(rows) + 1):
        ch = _LateW(rows, w)
        claims = [seq for seq, r in rows[:w] if r.topic == "lifecycle.started"]
        v = verdict(ch)
        if v is not None:
            assert v.claim_seq == (claims[-1] if claims else None)
        stops = {seq for seq, r in rows[:w] if r.topic == "control.stop"}
        assert set(undischarged_stops(ch)) <= stops
