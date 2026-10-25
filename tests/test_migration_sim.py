"""The generative model of conforming 0.3.0 writers (``tests/migration_sim``).

Four guarantees:

1. **Schema-valid logs.** Every rendered log validates against the 0.3.0 schemas:
   envelope-v0.2 and the convention for its topic, as ``test_schema.py`` checks
   the reference Worker's.
2. **Well-formed truths.** Every truth meets commits-and-lineage.md §2's
   structural rules (``truth.violations``).
3. **The reference Worker lies inside the model.** Driven through a clean run, a
   kill mid-step, a resume and a cooperative stop, its log equals, after ``t``
   and ids are normalised, the log of a history the enumeration generates.
4. **The named histories are generable.** The histories §7's ambiguity entries
   and goldens name, and the d78b72f1 shape, are conforming histories; the ones
   an entry says share a log do share it; and each truth is what its entry says
   it needs.

Every enumeration stays under 2,000 cases and streams.
"""

from __future__ import annotations

import inspect
import json
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple
from unittest.mock import patch

import pytest

from runstate.channel import Channel, create_channel
from runstate.worker import Worker
from tests.migration_sim.build import (
    VALUE,
    Cut,
    Saving,
    Shape,
    Step,
    Tick,
    iteration,
    run,
)
from tests.migration_sim.enumerate import (
    COMPLETES,
    KILL,
    PREEMPTED,
    RESUMES,
    STOPS_AND_REAPS,
    THIRD_PARTIES,
    WORKER_SHAPES,
    LaunchShape,
    Origins,
    Space,
    StopSlot,
    histories,
)
from tests.migration_sim.history import (
    Beacon,
    Beat,
    Begin,
    Claim,
    CommitOf,
    Emit,
    Event,
    Exit,
    Exited,
    ExitOf,
    FinalStep,
    Finish,
    FromOutside,
    FromSave,
    Fresh,
    Halt,
    History,
    Honors,
    Killed,
    Launched,
    NotConforming,
    Outcome,
    Reaped,
    Release,
    Report,
    Rewind,
    Save,
    StopRequest,
    ThirdPartyFinal,
    ThirdPartyStop,
    walk,
)
from tests.migration_sim.render import Rendered, records, render
from tests.migration_sim.truth import (
    Commit,
    CommitId,
    Died,
    ExitNode,
    NodeId,
    RewindTo,
    Start,
    Terminated,
    Truth,
    Withdrawal,
    place_key,
    steps,
    truth,
    violations,
)

# ----- the families enumerated -------------------------------------------------------


class Family(NamedTuple):
    claims: int
    steps: int
    stops: int
    space: Space


def _histories(f: Family) -> Iterator[History]:
    return histories(claims=f.claims, steps=f.steps, stops=f.stops, space=f.space)


FAMILIES = {
    "worker-shapes": Family(1, 2, 0, WORKER_SHAPES),
    "resumes": Family(2, 2, 0, RESUMES),
    "third-parties": Family(2, 1, 0, THIRD_PARTIES),
    "stops-and-reaps": Family(2, 1, 1, STOPS_AND_REAPS),
}
BOUND = 2000


@pytest.mark.parametrize("family", FAMILIES.values(), ids=list(FAMILIES))
def test_enumeration_is_bounded_and_streams(family: Family) -> None:
    stream = _histories(family)
    assert inspect.isgenerator(stream)
    next(stream)  # a history arrives before the rest are enumerated
    assert 1 + sum(1 for _ in stream) < BOUND


@pytest.mark.parametrize("family", FAMILIES.values(), ids=list(FAMILIES))
def test_every_family_shares_logs_between_truths(family: Family) -> None:
    """Each family is rich enough to generate an ambiguous log: two histories
    that write it and need different outputs."""
    lineages = defaultdict(set)
    for h in _histories(family):
        lineages[render(h).rows].add(truth(h).lineage)
    assert any(len(ls) > 1 for ls in lineages.values())


# ----- 1. schema-valid logs ----------------------------------------------------------

_PROTO = Path(__file__).resolve().parent.parent / "protocol"


@pytest.fixture(scope="module")
def conforms_to_0_3_0() -> Callable[[Rendered], None]:
    jsonschema = pytest.importorskip("jsonschema")

    def validator(name: str) -> Any:
        schema = json.loads((_PROTO / f"{name}.schema.json").read_text())
        return jsonschema.Draft202012Validator(schema)

    envelope = validator("envelope-v0.2")
    conventions = {
        "control.": validator("subscription-v0.3"),
        "lifecycle.": validator("lifecycle-v0.5"),
        "launcher.": validator("launcher-v0.4"),
        "value": validator("value-v0.2"),
    }

    def check(rendered: Rendered) -> None:
        for record in records(rendered):
            envelope.validate(record)
            (convention,) = [
                v
                for prefix, v in conventions.items()
                if record["topic"].startswith(prefix)
            ]
            convention.validate(record)

    return check


@pytest.mark.parametrize("family", FAMILIES.values(), ids=list(FAMILIES))
def test_every_rendered_log_conforms_to_the_0_3_0_schemas(
    family: Family, conforms_to_0_3_0: Callable[[Rendered], None]
) -> None:
    for h in _histories(family):
        conforms_to_0_3_0(render(h))


# ----- 2. well-formed truths ---------------------------------------------------------


@pytest.mark.parametrize("family", FAMILIES.values(), ids=list(FAMILIES))
def test_every_truth_is_well_formed(family: Family) -> None:
    for h in _histories(family):
        assert violations(truth(h)) == (), h


def _with(t: Truth, bad: Commit) -> Truth:
    """``t`` with ``bad`` in place of the commit it names."""
    return t._replace(
        places=tuple(
            (
                p._replace(record=bad)
                if isinstance(p.record, Commit) and p.record.node == bad.node
                else p
            )
            for p in t.places
        )
    )


def test_the_structural_check_catches_each_rule() -> None:
    """``violations`` is not vacuous: a truth broken each way is caught."""
    t = truth(_h(Claim(0, Fresh(), None), run(0, range(2), SAVES), _killed(0)))
    first, second = t.lineage.commits
    assert (first.values, second.values) == ((2,), (4,))  # the values' seqs
    for bad, rule in [
        (second._replace(parent=CommitId(1, 7)), "not an extendable node"),
        (first._replace(parent=second.node), "does not precede it"),
        (second._replace(values=(2, 4)), "named twice"),
        (first._replace(values=(4,)), "follows it"),
    ]:
        assert any(rule in v for v in violations(_with(t, bad))), rule
    exited = truth(STOP_AUTHOR_EXIT)
    twice = exited._replace(places=exited.places + exited.places[-1:])
    assert any("2 exits" in v for v in violations(twice))


# ----- 3. the reference Worker -------------------------------------------------------

SAVES = Shape(valued=True, tick=Tick.AFTER_BODY, save=Saving.IN_BODY, trailing=False)
"""The ``steps()`` worker that emits each iteration and saves in its body."""

WORKER = Space(
    origins=(Origins.FRESH, Origins.SAVED),
    loading=(False,),
    strides=(1,),
    shapes=(SAVES,),
    cuts=(Cut.MID_BODY, Cut.DONE),
    endings=(KILL, COMPLETES, PREEMPTED),
    displaced=(),
    third_parties=(None,),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(StopSlot.BEFORE_CLAIM,),
)


def _clean(ch: Channel) -> None:
    with Worker(ch) as w:
        for s in w.steps(3):
            w.emit(VALUE, f"{VALUE}@{s}")
        w.stopped(completed=True)


def _killed_mid_step(ch: Channel) -> None:
    w = Worker(ch)
    for s in w.steps(5):
        w.emit(VALUE, f"{VALUE}@{s}")
        if s == 2:
            break  # the process dies here: nothing more of it reaches the log


def _resumed(ch: Channel) -> None:
    # Each claim is its own process, as the model's are: a resume relaunches.
    with patch("runstate.worker.local_handle", return_value="local://a/1"):
        with Worker(ch) as w:  # its __exit__ writes the preempted exit
            for s in w.steps(2):
                w.emit(VALUE, f"{VALUE}@{s}")
    with patch("runstate.worker.local_handle", return_value="local://b/2"):
        with Worker(ch) as w:
            for s in w.steps(4, start=2):
                w.emit(VALUE, f"{VALUE}@{s}")
            w.stopped(completed=True)


def _stopped_cooperatively(ch: Channel) -> None:
    ch.send({}, topic="control.stop", request_id="halt")
    with Worker(ch) as w:
        for s in w.steps(5):
            w.emit(VALUE, f"{VALUE}@{s}")


class Scenario(NamedTuple):
    drive: Callable[[Channel], None]
    family: Family  # the histories searched for the Worker's log


SCENARIOS = {
    "clean run": Scenario(_clean, Family(1, 3, 0, WORKER)),
    "kill mid-step": Scenario(_killed_mid_step, Family(1, 3, 0, WORKER)),
    "resume": Scenario(_resumed, Family(2, 2, 0, WORKER)),
    "cooperative stop": Scenario(_stopped_cooperatively, Family(1, 1, 1, WORKER)),
}


def _normalised(
    log: list[dict[str, Any]],
) -> list[tuple[str, str | None, str | None, str]]:
    """The log with every ``t`` dropped, and handles and request ids renamed by
    first appearance."""
    ids: dict[str, str] = {}

    def rename(x: str) -> str:
        return ids.setdefault(x, f"id{len(ids)}")

    out = []
    for r in log:
        body = {k: v for k, v in r["body"].items() if k != "t"}
        if "handle" in body:
            body["handle"] = rename(body["handle"])
        if "honored" in body:
            body["honored"] = [rename(x) for x in body["honored"]]
        rid = None if r["request_id"] is None else rename(r["request_id"])
        out.append((r["topic"], r["name"], rid, json.dumps(body, sort_keys=True)))
    return out


@pytest.mark.parametrize("scenario", SCENARIOS.values(), ids=list(SCENARIOS))
def test_the_reference_worker_lies_inside_the_model(scenario: Scenario) -> None:
    ch = create_channel(f"sim-{uuid.uuid4().hex}", root=None, backend="memory")
    scenario.drive(ch)
    worker_log = _normalised(
        [
            {
                "topic": e.topic,
                "name": e.name,
                "request_id": e.request_id,
                "body": e.body,
            }
            for e in ch.read()
        ]
    )
    n = 0
    for h in _histories(scenario.family):
        n += 1
        if _normalised(records(render(h))) == worker_log:
            break
    else:
        pytest.fail(f"no history of {n} renders the Worker's log {worker_log}")
    assert n < BOUND


# ----- 4. the named histories --------------------------------------------------------

BEATS = Shape(valued=False, tick=Tick.AFTER_BODY, save=Saving.IN_BODY, trailing=False)
FIRST = Shape(valued=False, tick=Tick.BEFORE_BODY, save=Saving.IN_BODY, trailing=False)
UNLOGGED = Shape(valued=False, tick=Tick.NONE, save=Saving.IN_BODY, trailing=False)
QUIET = Shape(valued=False, tick=Tick.NONE, save=Saving.NONE, trailing=False)
UNSAVED = Shape(valued=True, tick=Tick.AFTER_BODY, save=Saving.NONE, trailing=False)
NULL, PROGRESS = ThirdPartyFinal.NULL, ThirdPartyFinal.PROGRESS
YIELDED = FinalStep.YIELDED


def _h(*parts: Event | list[Event]) -> History:
    events: list[Event] = []
    for part in parts:
        events += part if isinstance(part, list) else [part]
    return History(tuple(events))


def _killed(claim: int) -> Halt:
    return Halt(claim, Killed(9))


def _completes(claim: int) -> list[Event]:
    return [Exit(claim, True, None, YIELDED), Halt(claim, Exited(0))]


def _released(claim: int, outcome: Outcome) -> ThirdPartyStop:
    return ThirdPartyStop(claim, Release(outcome), None, NULL, Honors.NONE)


# stop-author: H1 the Worker's chunk-cap exit honouring x; H2 a tool's same body
# for a claim killed with its node, honouring the pending x.
STOP_AUTHOR_EXIT = _h(
    Claim(0, Fresh(), None),
    run(0, range(3), SAVES),
    StopRequest("x0"),
    Exit(0, False, None, YIELDED),
    Halt(0, Exited(0)),
)
STOP_AUTHOR_THIRD_PARTY = _h(
    Claim(0, Fresh(), None),
    run(0, range(3), SAVES),
    _killed(0),
    StopRequest("x0"),
    ThirdPartyStop(0, Release(Outcome.LOST), None, PROGRESS, Honors.UNDISCHARGED),
)


# third-party-stop. Its H1 (after the worker's exit) and H2 (a dead claim) are
# different logs as written, so each is paired with a same-log counterpart.
def _after_exit(intent: Release | Report) -> History:
    return _h(
        Claim(0, Fresh(), None),
        run(0, range(2), SAVES),
        Exit(0, False, None, YIELDED),
        Halt(0, Exited(0)),
        ThirdPartyStop(0, intent, None, NULL, Honors.NONE),
    )


def _after_death(intent: Release | Report) -> History:
    return _h(
        Claim(0, Fresh(), None),
        run(0, range(2), SAVES),
        _killed(0),
        ThirdPartyStop(0, intent, None, NULL, Honors.NONE),
    )


TPS_CLEARS_STAGED = _h(
    Claim(0, Fresh(), None),
    run(0, range(2), SAVES),
    Exit(0, False, None, YIELDED),
    Halt(0, Exited(0)),
    StopRequest("x0"),
    ThirdPartyStop(0, Report(None), None, NULL, Honors.UNDISCHARGED),
)
TPS_NOTE_AFTER_EXIT = _after_exit(Report(None))
TPS_LOSER_REAPED_AFTER_EXIT = _after_exit(Report(Exited(1)))
TPS_RELEASE_AFTER_EXIT = _after_exit(Release(Outcome.LOST))
TPS_RELEASE_OF_DEAD = _after_death(Release(Outcome.LOST))
TPS_NOTE_ON_DEAD = _after_death(Report(None))

# value-claim: H1 the successor's own value; H2 a released worker's late value,
# and H2' an exited worker's teardown value, after the successor claimed.
_VC_FIRST = [Claim(0, Fresh(), None), *run(0, range(2), SAVES), Begin(0, 2)]
_VC_SUCCESSOR = Claim(1, FromSave(CommitOf(0, 1)), None)
VALUE_CLAIM_SUCCESSORS = _h(
    _VC_FIRST,
    _killed(0),
    _released(0, Outcome.LOST),
    _VC_SUCCESSOR,
    iteration(1, Step(2, SAVES), cut=Cut.DONE),
    _completes(1),
)
VALUE_CLAIM_DISPLACED = _h(
    _VC_FIRST,
    _released(0, Outcome.LOST),
    _VC_SUCCESSOR,
    Emit(0, VALUE, stepless=False),
    _killed(0),
    iteration(1, Step(2, BEATS), cut=Cut.DONE),
    _completes(1),
)
VALUE_CLAIM_EXITED_SUCCESSORS = _h(
    _VC_FIRST,
    Exit(0, False, None, YIELDED),
    Halt(0, Exited(0)),
    _VC_SUCCESSOR,
    iteration(1, Step(2, SAVES), cut=Cut.DONE),
    _completes(1),
)
VALUE_CLAIM_TEARDOWN = _h(
    _VC_FIRST,
    Exit(0, False, None, YIELDED),
    _VC_SUCCESSOR,
    Emit(0, VALUE, stepless=False),
    Halt(0, Exited(0)),
    iteration(1, Step(2, BEATS), cut=Cut.DONE),
    _completes(1),
)

# beat-ends-iteration: H1 steps() killed in step 3's body; H2 a tick-first loop
# killed in step 2's body.
BEAT_ENDS = _h(
    Claim(0, Fresh(), None), run(0, range(3), BEATS), Begin(0, 3), _killed(0)
)
BEAT_STARTS = _h(
    Claim(0, Fresh(), None), run(0, range(2), FIRST), Begin(0, 2), Beat(0), _killed(0)
)

# post-beat-values: H1 a tick-first loop's metrics after tick(2); H2 a steps()
# worker's post-loop value.
POST_BEAT_LABEL = _h(
    Claim(0, Fresh(), None),
    run(0, range(2), FIRST),
    Begin(0, 2),
    Beat(0),
    Emit(0, VALUE, stepless=False),
    Finish(0, True),
    Exit(0, False, None, YIELDED),
    Halt(0, Exited(0)),
)
POST_BEAT_EXIT_HOLDS = _h(
    Claim(0, Fresh(), None),
    run(0, range(3), BEATS),
    Emit(0, VALUE, stepless=False),
    Exit(0, False, None, YIELDED),
    Halt(0, Exited(0)),
)

# leading-values: H1 a loading status at the resume step 4; H2 an unticked step 4
# after a resume from 3.
_LV_BASE = [Claim(0, Fresh(), None), *run(0, range(5), SAVES), _killed(0)]
LEADING_LOADING = _h(
    _LV_BASE,
    Claim(1, FromSave(CommitOf(0, 4)), None),
    Emit(1, VALUE, stepless=False),
    iteration(1, Step(5, BEATS), cut=Cut.DONE),
    _completes(1),
)
LEADING_UNTICKED = _h(
    _LV_BASE,
    Claim(1, FromSave(CommitOf(0, 3)), None),
    Begin(1, 4),
    Emit(1, VALUE, stepless=False),
    Finish(1, True),
    iteration(1, Step(5, BEATS), cut=Cut.DONE),
    _completes(1),
)

# beatless-claim: H1 a claim that crashed while loading, its status at 3; H2 one
# that finished step 3 and saved it, and the next claim started at 4.
_BC_BASE = [Claim(0, Fresh(), None), *run(0, range(4), SAVES), _killed(0)]


def _bc_last(origin: CommitOf) -> list[Event]:
    return [
        Claim(2, FromSave(origin), None),
        *iteration(2, Step(4, SAVES), cut=Cut.DONE),
        *_completes(2),
    ]


BEATLESS_LOADING = _h(
    _BC_BASE,
    Claim(1, FromSave(CommitOf(0, 3)), None),
    Emit(1, VALUE, stepless=False),
    _killed(1),
    _bc_last(CommitOf(0, 3)),
)
BEATLESS_FINISHED = _h(
    _BC_BASE,
    Claim(1, FromSave(CommitOf(0, 2)), None),
    iteration(1, Step(3, SAVES), cut=Cut.BEFORE_TICK),
    _killed(1),
    _bc_last(CommitOf(1, 0)),
)


# d78b72f1: claim 1 beats 0-180, writes 181 and is killed while saving; a beatless
# claim resumes at 182 and crashes saving; a beatless claim resumes at 183 and
# exits completed with final_step 183. The run's later resumes say that 181 and
# 182 finished (§7, beatless-claim H2); D78_181_OPEN is the same log where 181
# did not, and the second claim ran 181 unlogged.
def _d78(first: list[Event], second: list[Event], nth: int) -> History:
    return _h(
        Claim(0, Fresh(), None),
        run(0, range(181), SAVES),
        first,
        _killed(0),
        second,
        Begin(1, 182),
        Emit(1, VALUE, stepless=False),
        Finish(1, True),
        Exit(1, False, "crashed while saving", FinalStep.TICKED),
        Halt(1, Exited(1)),
        Claim(2, FromSave(CommitOf(1, nth)), None),  # claim 1's save of 182
        Begin(2, 183),
        Emit(2, VALUE, stepless=False),
        Finish(2, True),
        Exit(2, True, None, FinalStep.FINISHED),
        Halt(2, Exited(0)),
    )


D78 = _d78(
    iteration(0, Step(181, SAVES), cut=Cut.BEFORE_TICK),
    [Claim(1, FromSave(CommitOf(0, 181)), None)],
    nth=0,
)
D78_181_OPEN = _d78(
    iteration(0, Step(181, SAVES), cut=Cut.MID_BODY),
    [Claim(1, FromSave(CommitOf(0, 180)), None), Begin(1, 181), Finish(1, False)],
    nth=1,
)

# valueless-gap: H1 an unticked, valueless iteration; H2 a stride.
GAP_ITERATION = _h(
    Claim(0, Fresh(), None),
    run(0, [0], BEATS),
    run(0, [1], UNLOGGED),
    run(0, [2], BEATS),
    _completes(0),
)
GAP_STRIDE = _h(Claim(0, Fresh(), None), run(0, [0, 2], BEATS), _completes(0))


# final-step: H1 the worker finished its final_step; H2 it had not. Clean
# exits, error exits, and a manual-tick worker whose final_step is its last tick.
def _final(finished: bool, error: str | None) -> History:
    return _h(
        Claim(0, Fresh(), None),
        run(0, range(3), SAVES),
        Begin(0, 3),
        Emit(0, VALUE, stepless=False),
        [Finish(0, True)] if finished else [],
        Exit(0, False, error, YIELDED),
        Halt(0, Exited(0 if error is None else 1)),
    )


def _final_ticked(finished: bool) -> History:
    return _h(
        Claim(0, Fresh(), None),
        run(0, range(3), SAVES),
        Begin(0, 3),
        Emit(0, VALUE, stepless=False),
        [Finish(0, True)] if finished else [],
        Exit(0, False, "crashed", FinalStep.TICKED),
        Halt(0, Exited(1)),
        Claim(1, FromSave(CommitOf(0, 3 if finished else 2)), None),
        iteration(1, Step(4, SAVES), cut=Cut.DONE),
        _completes(1),
    )


FINAL_FINISHED = _final(True, None)
FINAL_OPEN = _final(False, None)
FINAL_FINISHED_TICK_RAISED = _final(True, "boom")
FINAL_OPEN_BODY_RAISED = _final(False, "boom")
FINAL_TICKED_FINISHED = _final_ticked(True)
FINAL_TICKED_OPEN = _final_ticked(False)

# exitless-step: H1 killed inside step 3's body; H2 finished and saved step 3,
# killed before its tick.
EXITLESS_OPEN = _h(
    Claim(0, Fresh(), None),
    run(0, range(3), SAVES),
    iteration(0, Step(3, SAVES), cut=Cut.MID_BODY),
    _killed(0),
)
EXITLESS_FINISHED = _h(
    Claim(0, Fresh(), None),
    run(0, range(3), SAVES),
    iteration(0, Step(3, SAVES), cut=Cut.BEFORE_TICK),
    _killed(0),
)


# resume-origin: C2 loaded C1's save at 0 (H1), or a checkpoint from outside (H2).
def _origin(origin: FromSave | FromOutside) -> History:
    return _h(
        Claim(0, Fresh(), None),
        run(0, [0], SAVES),
        _killed(0),
        Claim(1, origin, None),
        run(1, [1, 2], SAVES),
        _completes(1),
    )


ORIGIN_IN_LOG = _origin(FromSave(CommitOf(0, 0)))
ORIGIN_OUTSIDE = _origin(FromOutside(0))

# resume-offset: C1 saved every step and C2 loaded 3 (H1); C1 saved every second
# step, and C2 loaded 2 and ran 3 unlogged, or skipped it.
_EVERY = [Claim(0, Fresh(), None), *run(0, range(4), SAVES), _killed(0)]
_ALTERNATE = [
    Claim(0, Fresh(), None),
    *run(0, [0], SAVES),
    *run(0, [1], UNSAVED),
    *run(0, [2], SAVES),
    *run(0, [3], UNSAVED),
    _killed(0),
]
OFFSET_PREVIOUS = _h(
    _EVERY, Claim(1, FromSave(CommitOf(0, 3)), None), run(1, [4], SAVES), _completes(1)
)
OFFSET_EARLIER_RAN = _h(
    _ALTERNATE,
    Claim(1, FromSave(CommitOf(0, 2)), None),
    run(1, [3], UNLOGGED),
    run(1, [4], SAVES),
    _completes(1),
)
OFFSET_EARLIER_SKIPPED = _h(
    _ALTERNATE,
    Claim(1, FromSave(CommitOf(0, 2)), None),
    run(1, [4], SAVES),
    _completes(1),
)

# resume-claim: H1/H2 both earlier claims beat 3; H3 two run-ons to 5; H4 a
# run-on to 5 beside a source of 5.
_RC_BASE = [
    Claim(0, Fresh(), None),
    *run(0, range(4), SAVES),
    _killed(0),
    Claim(1, FromSave(CommitOf(0, 2)), None),
    *run(1, [3], SAVES),
    _killed(1),
]
_RC_H3 = [
    Claim(0, Fresh(), None),
    *run(0, range(4), BEATS),
    *run(0, [4], QUIET),
    *run(0, [5], UNLOGGED),
    _killed(0),
    Claim(1, Fresh(), None),
    *run(1, range(2), BEATS),
    *run(1, [2, 3, 4], QUIET),
    *run(1, [5], UNLOGGED),
    _killed(1),
]
_RC_H4 = [
    Claim(0, Fresh(), None),
    *run(0, range(6), BEATS),
    _killed(0),
    Claim(1, Fresh(), None),
    *run(1, range(4), BEATS),
    *run(1, [4], QUIET),
    *run(1, [5], UNLOGGED),
    _killed(1),
]


def _third_from(base: list[Event], origin: CommitOf, step: int) -> History:
    return _h(
        base, Claim(2, FromSave(origin), None), run(2, [step], SAVES), _completes(2)
    )


CLAIM_NEWER = _third_from(_RC_BASE, CommitOf(1, 0), 4)
CLAIM_OLDER = _third_from(_RC_BASE, CommitOf(0, 3), 4)
CLAIM_RUN_ON_NEWER = _third_from(_RC_H3, CommitOf(1, 5), 6)
CLAIM_RUN_ON_OLDER = _third_from(_RC_H3, CommitOf(0, 5), 6)
CLAIM_RUN_ON_SAVER = _third_from(_RC_H4, CommitOf(1, 5), 6)
CLAIM_RUN_ON_SOURCE = _third_from(_RC_H4, CommitOf(0, 5), 6)


# commitless-resume: J loaded A's newest save (H1), or the one file that held A's
# save at 1, or nothing (H2); B then resumed from A's 1.
def _commitless(origin: FromSave | Fresh) -> History:
    return _h(
        Claim(0, Fresh(), None),
        run(0, range(4), SAVES),
        _killed(0),
        Claim(1, origin, None),
        _killed(1),
        Claim(2, FromSave(CommitOf(0, 1)), None),
        run(2, [2], SAVES),
        _completes(2),
    )


COMMITLESS_NEWEST = _commitless(FromSave(CommitOf(0, 3)))
COMMITLESS_ONE_FILE = _commitless(FromSave(CommitOf(0, 1)))
COMMITLESS_NOTHING = _commitless(Fresh())


# LATER_COMPLETED: beats 0-3; stop x; a default-body stop honouring x; a value at
# 4; a completed stop. The third party cleared x (a report) or, mistakenly,
# released the live claim.
def _later_completed(intent: Release | Report, finished: bool) -> History:
    return _h(
        Claim(0, Fresh(), None),
        run(0, range(4), SAVES),
        StopRequest("x0"),
        ThirdPartyStop(0, intent, None, NULL, Honors.UNDISCHARGED),
        Begin(0, 4),
        Emit(0, VALUE, stepless=False),
        [Finish(0, True)] if finished else [],
        _completes(0),
    )


LATER_COMPLETED_FINISHED = _later_completed(Report(None), finished=True)
LATER_COMPLETED_YIELDED = _later_completed(Report(None), finished=False)
LATER_COMPLETED_RELEASED = _later_completed(Release(Outcome.LOST), finished=True)


# BEATEN_PAST: beats 0-2; a stopped with an error; a value at 3; a beat at 3; a
# completed stop. The error stop was a note, or a mistaken release.
def _beaten_past(intent: Release | Report) -> History:
    return _h(
        Claim(0, Fresh(), None),
        run(0, range(3), SAVES),
        ThirdPartyStop(0, intent, "job 7 FAILED", NULL, Honors.NONE),
        run(0, [3], SAVES),
        _completes(0),
    )


BEATEN_PAST_NOTE = _beaten_past(Report(None))
BEATEN_PAST_RELEASED = _beaten_past(Release(Outcome.ERRORED))


# STOPPED_AFTER_ITS_REAP: launched L; the claim with L's handle; values and beats
# 0-2; a value at 3; L killed by signal 9; a stop x; a default body honouring x.
def _after_reap(finished: bool) -> History:
    return _h(
        Launched("L0", wrapper=False),
        Claim(0, Fresh(), "L0"),
        run(0, range(3), SAVES),
        iteration(0, Step(3, SAVES), cut=Cut.BEFORE_TICK if finished else Cut.MID_BODY),
        _killed(0),
        Reaped("L0", Killed(9)),
        StopRequest("x0"),
        ThirdPartyStop(0, Report(None), None, NULL, Honors.UNDISCHARGED),
    )


STOPPED_AFTER_ITS_REAP = _after_reap(finished=False)
STOPPED_AFTER_ITS_REAP_FINISHED = _after_reap(finished=True)

# SUCCESSOR_PROVED_DEAD: claim 1 beats 0 and 1; claim 2 beats 2 and exits
# completed at 2.
SUCCESSOR_PROVED_DEAD = _h(
    Claim(0, Fresh(), None),
    run(0, range(2), BEATS),
    _killed(0),
    Claim(1, FromSave(CommitOf(0, 1)), None),
    run(1, [2], BEATS),
    _completes(1),
)

# THIRD_PLACE_MOVES_PAST: A beats 0-4 with a value at each and writes loss@6, and
# nothing ends it. B writes loss@5 and loss@7, and an error stopped with a null
# final_step. L0 writes loss@6, beats 6, and nothing ends it. L1 writes loss@8,
# beats 8 and exits completed with final_step 8. The history its stated output
# needs: A ran 5 unlogged and was killed in 6; B resumed from A's 4 and ran 5 to
# 7, 6 unlogged; L0 resumed from B's 5, L1 from B's 7.
THIRD_PLACE_MOVES_PAST = _h(
    Claim(0, Fresh(), None),
    run(0, range(5), SAVES),
    run(0, [5], UNLOGGED),
    iteration(0, Step(6, SAVES), cut=Cut.MID_BODY),
    _killed(0),
    Claim(1, FromSave(CommitOf(0, 4)), None),
    iteration(1, Step(5, SAVES), cut=Cut.BEFORE_TICK),
    run(1, [6], UNLOGGED),
    iteration(1, Step(7, SAVES), cut=Cut.BEFORE_TICK),
    Exit(1, False, "boom", FinalStep.TICKED),
    Halt(1, Exited(1)),
    Claim(2, FromSave(CommitOf(1, 0)), None),
    run(2, [6], SAVES),
    _killed(2),
    Claim(3, FromSave(CommitOf(1, 2)), None),
    run(3, [8], SAVES),
    _completes(3),
)

# Two shapes §7 does not name, where §2's truth is one §7's rules cannot give
# (the task report, "Disagreements").
# A released worker finished and saved 2 before the release; its beat at 2 lands
# after its successor claimed, and the successor resumed from that commit.
DISPLACED_SAVED_FIRST = _h(
    Claim(0, Fresh(), None),
    run(0, range(2), SAVES),
    iteration(0, Step(2, SAVES), cut=Cut.BEFORE_TICK),
    _released(0, Outcome.LOST),
    Claim(1, FromSave(CommitOf(0, 2)), None),
    Beat(0),
    _killed(0),
    run(1, [3], SAVES),
    _completes(1),
)
# The same log, where 2 finished only after the release (fenced) and the
# successor resumed from 1 and ran 2 unlogged.
DISPLACED_SAVED_AFTER = _h(
    Claim(0, Fresh(), None),
    run(0, range(2), SAVES),
    iteration(0, Step(2, SAVES), cut=Cut.MID_BODY),
    _released(0, Outcome.LOST),
    Claim(1, FromSave(CommitOf(0, 1)), None),
    Finish(0, True),
    Beat(0),
    _killed(0),
    run(1, [2], UNLOGGED),
    run(1, [3], SAVES),
    _completes(1),
)


# A worker saved after its own preempted exit (mycooc's order before b5262327);
# the next claim loaded that save, the exit node, or the last commit's.
def _post_exit_save(origin: CommitOf | ExitOf) -> History:
    return _h(
        Claim(0, Fresh(), None),
        run(0, range(3), SAVES),
        Emit(0, VALUE, stepless=False),
        Exit(0, False, None, YIELDED),
        Save(0),
        Halt(0, Exited(0)),
        Claim(1, FromSave(origin), None),
        run(1, [3], SAVES),
        _completes(1),
    )


POST_EXIT_SAVE = _post_exit_save(ExitOf(0))
POST_EXIT_COMMIT = _post_exit_save(CommitOf(0, 2))

NAMED = {
    "stop-author H1": STOP_AUTHOR_EXIT,
    "stop-author H2": STOP_AUTHOR_THIRD_PARTY,
    "third-party-stop H1": TPS_CLEARS_STAGED,
    "third-party-stop H1, a note after the exit": TPS_NOTE_AFTER_EXIT,
    "third-party-stop H2, a loser's reap after the exit": TPS_LOSER_REAPED_AFTER_EXIT,
    "third-party-stop, a release after the exit": TPS_RELEASE_AFTER_EXIT,
    "third-party-stop H2, a release of a dead claim": TPS_RELEASE_OF_DEAD,
    "third-party-stop, a note on a dead claim": TPS_NOTE_ON_DEAD,
    "value-claim H1": VALUE_CLAIM_SUCCESSORS,
    "value-claim H2": VALUE_CLAIM_DISPLACED,
    "value-claim H1, after an exit": VALUE_CLAIM_EXITED_SUCCESSORS,
    "value-claim H2, a teardown value": VALUE_CLAIM_TEARDOWN,
    "beat-ends-iteration H1": BEAT_ENDS,
    "beat-ends-iteration H2": BEAT_STARTS,
    "post-beat-values H1": POST_BEAT_LABEL,
    "post-beat-values H2": POST_BEAT_EXIT_HOLDS,
    "leading-values H1": LEADING_LOADING,
    "leading-values H2": LEADING_UNTICKED,
    "beatless-claim H1": BEATLESS_LOADING,
    "beatless-claim H2": BEATLESS_FINISHED,
    "d78b72f1": D78,
    "d78b72f1, 181 unfinished": D78_181_OPEN,
    "valueless-gap H1": GAP_ITERATION,
    "valueless-gap H2": GAP_STRIDE,
    "final-step H1": FINAL_FINISHED,
    "final-step H2": FINAL_OPEN,
    "final-step H1, tick raised": FINAL_FINISHED_TICK_RAISED,
    "final-step H2, body raised": FINAL_OPEN_BODY_RAISED,
    "final-step H1, manual tick": FINAL_TICKED_FINISHED,
    "final-step H2, manual tick": FINAL_TICKED_OPEN,
    "exitless-step H1": EXITLESS_OPEN,
    "exitless-step H2": EXITLESS_FINISHED,
    "resume-origin H1": ORIGIN_IN_LOG,
    "resume-origin H2": ORIGIN_OUTSIDE,
    "resume-offset H1": OFFSET_PREVIOUS,
    "resume-offset H2, ran": OFFSET_EARLIER_RAN,
    "resume-offset H2, skipped": OFFSET_EARLIER_SKIPPED,
    "resume-claim H1": CLAIM_NEWER,
    "resume-claim H2": CLAIM_OLDER,
    "resume-claim H3, newer": CLAIM_RUN_ON_NEWER,
    "resume-claim H3, older": CLAIM_RUN_ON_OLDER,
    "resume-claim H4": CLAIM_RUN_ON_SAVER,
    "resume-claim H4, the source": CLAIM_RUN_ON_SOURCE,
    "commitless-resume H1": COMMITLESS_NEWEST,
    "commitless-resume H2, one file": COMMITLESS_ONE_FILE,
    "commitless-resume H2, nothing": COMMITLESS_NOTHING,
    "LATER_COMPLETED, finished": LATER_COMPLETED_FINISHED,
    "LATER_COMPLETED, yielded": LATER_COMPLETED_YIELDED,
    "LATER_COMPLETED, released": LATER_COMPLETED_RELEASED,
    "BEATEN_PAST": BEATEN_PAST_NOTE,
    "BEATEN_PAST, released": BEATEN_PAST_RELEASED,
    "STOPPED_AFTER_ITS_REAP": STOPPED_AFTER_ITS_REAP,
    "STOPPED_AFTER_ITS_REAP, finished": STOPPED_AFTER_ITS_REAP_FINISHED,
    "SUCCESSOR_PROVED_DEAD": SUCCESSOR_PROVED_DEAD,
    "THIRD_PLACE_MOVES_PAST": THIRD_PLACE_MOVES_PAST,
    "displaced, saved before the release": DISPLACED_SAVED_FIRST,
    "displaced, finished after the release": DISPLACED_SAVED_AFTER,
    "post-exit save": POST_EXIT_SAVE,
    "post-exit save, the commit's": POST_EXIT_COMMIT,
}


class Shared(NamedTuple):
    """Two named histories that write one log."""

    one: str
    other: str


AMBIGUOUS = [
    Shared("stop-author H1", "stop-author H2"),
    Shared(
        "third-party-stop H1, a note after the exit",
        "third-party-stop H2, a loser's reap after the exit",
    ),
    Shared(
        "third-party-stop H2, a release of a dead claim",
        "third-party-stop, a note on a dead claim",
    ),
    Shared("value-claim H1", "value-claim H2"),
    Shared("value-claim H1, after an exit", "value-claim H2, a teardown value"),
    Shared("beat-ends-iteration H1", "beat-ends-iteration H2"),
    Shared("post-beat-values H1", "post-beat-values H2"),
    Shared("leading-values H1", "leading-values H2"),
    Shared("beatless-claim H1", "beatless-claim H2"),
    Shared("d78b72f1", "d78b72f1, 181 unfinished"),
    Shared("valueless-gap H1", "valueless-gap H2"),
    Shared("final-step H1", "final-step H2"),
    Shared("final-step H1, tick raised", "final-step H2, body raised"),
    Shared("final-step H1, manual tick", "final-step H2, manual tick"),
    Shared("exitless-step H1", "exitless-step H2"),
    Shared("resume-origin H1", "resume-origin H2"),
    Shared("resume-offset H1", "resume-offset H2, ran"),
    Shared("resume-offset H1", "resume-offset H2, skipped"),
    Shared("resume-offset H2, ran", "resume-offset H2, skipped"),
    Shared("resume-claim H1", "resume-claim H2"),
    Shared("resume-claim H3, newer", "resume-claim H3, older"),
    Shared("resume-claim H4", "resume-claim H4, the source"),
    Shared("commitless-resume H1", "commitless-resume H2, one file"),
    Shared("commitless-resume H1", "commitless-resume H2, nothing"),
    Shared("LATER_COMPLETED, finished", "LATER_COMPLETED, yielded"),
    Shared("LATER_COMPLETED, finished", "LATER_COMPLETED, released"),
    Shared("BEATEN_PAST", "BEATEN_PAST, released"),
    Shared("STOPPED_AFTER_ITS_REAP", "STOPPED_AFTER_ITS_REAP, finished"),
    Shared(
        "displaced, saved before the release", "displaced, finished after the release"
    ),
    Shared("post-exit save", "post-exit save, the commit's"),
]
"""Same log, different truths: each pair needs a site."""

SETTLED = [
    Shared(
        "third-party-stop H1, a note after the exit",
        "third-party-stop, a release after the exit",
    ),
]
"""Same log, same truth: a native report_death writes nothing on a claim whose
exit is readable, so its third party's meaning does not matter there."""


@pytest.mark.parametrize("name", list(NAMED))
def test_every_named_history_is_generable(
    name: str, conforms_to_0_3_0: Callable[[Rendered], None]
) -> None:
    h = NAMED[name]
    walk(h)  # raises NotConforming for a history no conforming writer produces
    conforms_to_0_3_0(render(h))
    assert violations(truth(h)) == ()


@pytest.mark.parametrize(
    "pair", AMBIGUOUS, ids=[f"{p.one} | {p.other}" for p in AMBIGUOUS]
)
def test_ambiguous_pairs_share_a_log_and_need_different_outputs(pair: Shared) -> None:
    one, other = NAMED[pair.one], NAMED[pair.other]
    assert render(one).rows == render(other).rows
    assert truth(one).lineage != truth(other).lineage


@pytest.mark.parametrize("pair", SETTLED, ids=[f"{p.one} | {p.other}" for p in SETTLED])
def test_settled_pairs_share_a_log_and_one_output(pair: Shared) -> None:
    one, other = NAMED[pair.one], NAMED[pair.other]
    assert render(one).rows == render(other).rows
    assert truth(one).lineage == truth(other).lineage


# ----- reading a truth ---------------------------------------------------------------


def describe(h: History) -> list[str]:
    """The truth as lines, in the native log's order: ``c1@4`` is claim 1's
    commit of the iteration labelled 4, ``c1:exit`` its exit node, and a value is
    its payload (``v@4``: the value labelled 4)."""
    t = truth(h)
    rendered = render(h)
    labels = steps(h)
    claims = {
        seq: ev.claim
        for ev, seq in zip(h.events, rendered.seqs)
        if isinstance(ev, Claim) and seq is not None
    }
    payloads = {
        r.seq: json.loads(r.body)["value"] for r in rendered.rows if r.topic == "value"
    }

    def node(n: NodeId | None) -> str:
        if n is None:
            return "root"
        if isinstance(n, CommitId):
            return f"c{claims[n.claim_seq]}@{labels[n]}"
        return f"c{claims[n.claim_seq]}:exit"

    def values(vs: tuple[int, ...] | None) -> str:
        return "null" if vs is None else "[" + ", ".join(payloads[v] for v in vs) + "]"

    lines = []
    for p in sorted(t.places, key=lambda p: place_key(p.place)):
        r = p.record
        if isinstance(r, Start):
            lines.append(f"c{claims[r.claim_seq]} starts from {node(r.resume_from)}")
        elif isinstance(r, Commit):
            lines.append(f"{node(r.node)} <- {node(r.parent)} {values(r.values)}")
        elif isinstance(r, ExitNode):
            honors = f" honors {', '.join(r.honored)}" if r.honored else ""
            lines.append(
                f"{node(r.node)} {r.kind.value} <- {node(r.parent)} {values(r.values)}{honors}"
            )
        elif isinstance(r, Died):
            lines.append(f"c{claims[r.claim_seq]} died {r.outcome.value}")
        elif isinstance(r, Terminated):
            lines.append(f"terminated {r.launch or 'minted'} {r.how}")
        elif isinstance(r, Withdrawal):
            lines.append(f"withdraw {r.request_id}")
        elif isinstance(r, RewindTo):
            lines.append(f"c{claims[r.claim_seq]} rewinds to {node(r.target)}")
    return lines


def test_stop_author() -> None:
    assert describe(STOP_AUTHOR_EXIT) == [
        "c0 starts from root",
        "c0@0 <- root [v@0]",
        "c0@1 <- c0@0 [v@1]",
        "c0@2 <- c0@1 [v@2]",
        "c0:exit extendable <- c0@2 [] honors x0",
    ]
    # the tool's stop is a third party's: no exit node, the stop withdrawn
    assert describe(STOP_AUTHOR_THIRD_PARTY)[4:] == ["c0 died lost", "withdraw x0"]


def test_third_party_stop() -> None:
    assert describe(TPS_CLEARS_STAGED)[-2:] == [
        "c0:exit extendable <- c0@1 []",
        "withdraw x0",
    ]
    assert describe(TPS_NOTE_AFTER_EXIT)[-1] == "c0:exit extendable <- c0@1 []"
    assert (
        describe(TPS_LOSER_REAPED_AFTER_EXIT)[-1] == "terminated minted Exited(code=1)"
    )
    assert describe(TPS_RELEASE_OF_DEAD)[-1] == "c0 died lost"
    assert describe(TPS_NOTE_ON_DEAD)[-1] == "c0@1 <- c0@0 [v@1]"  # held for good


def test_value_claim() -> None:
    assert "c1@2 <- c0@1 [v@2]" in describe(VALUE_CLAIM_SUCCESSORS)
    assert "c1@2 <- c0@1 []" in describe(VALUE_CLAIM_DISPLACED)
    assert "c1@2 <- c0@1 [v@2]" in describe(VALUE_CLAIM_EXITED_SUCCESSORS)
    assert "c1@2 <- c0@1 []" in describe(VALUE_CLAIM_TEARDOWN)


def test_beat_ends_iteration() -> None:
    assert describe(BEAT_ENDS) == [
        "c0 starts from root",
        "c0@0 <- root []",
        "c0@1 <- c0@0 []",
        "c0@2 <- c0@1 []",
    ]
    assert describe(BEAT_STARTS) == [
        "c0 starts from root",
        "c0@0 <- root []",
        "c0@1 <- c0@0 []",
    ]


def test_post_beat_values() -> None:
    assert describe(POST_BEAT_LABEL)[-2:] == [
        "c0@2 <- c0@1 [v@2]",
        "c0:exit extendable <- c0@2 []",
    ]
    assert describe(POST_BEAT_EXIT_HOLDS)[-2:] == [
        "c0@2 <- c0@1 []",
        "c0:exit extendable <- c0@2 [v@2]",
    ]


def test_leading_values() -> None:
    assert describe(LEADING_LOADING)[-4:] == [
        "c0 died lost",
        "c1 starts from c0@4",
        "c1@5 <- c0@4 [v@4]",
        "c1:exit closed <- c1@5 []",
    ]
    assert describe(LEADING_UNTICKED)[-5:] == [
        "c0 died lost",
        "c1 starts from c0@3",
        "c1@4 <- c0@3 [v@4]",
        "c1@5 <- c1@4 []",
        "c1:exit closed <- c1@5 []",
    ]


def test_beatless_claim() -> None:
    assert describe(BEATLESS_LOADING)[-6:] == [
        "c0 died lost",
        "c1 starts from c0@3",
        "c1 died lost",
        "c2 starts from c0@3",
        "c2@4 <- c0@3 [v@4]",
        "c2:exit closed <- c2@4 []",
    ]
    assert describe(BEATLESS_FINISHED)[-7:] == [
        "c0 died lost",
        "c1 starts from c0@2",
        "c1@3 <- c0@2 [v@3]",
        "c1 died lost",
        "c2 starts from c1@3",
        "c2@4 <- c1@3 [v@4]",
        "c2:exit closed <- c2@4 []",
    ]


def test_d78b72f1() -> None:
    """181 and 182 finished: each later claim resumed from the one before."""
    lines = describe(D78)
    assert "c0@181 <- c0@180 [v@181]" in lines
    assert lines[-7:] == [
        "c0 died lost",
        "c1 starts from c0@181",
        "c1@182 <- c0@181 [v@182]",
        "c1:exit torn <- c1@182 null",
        "c2 starts from c1@182",
        "c2@183 <- c1@182 [v@183]",
        "c2:exit closed <- c2@183 []",
    ]
    assert describe(D78_181_OPEN)[-7:-3] == [
        "c1 starts from c0@180",
        "c1@181 <- c0@180 []",
        "c1@182 <- c1@181 [v@182]",
        "c1:exit torn <- c1@182 null",
    ]


def test_valueless_gap() -> None:
    assert describe(GAP_ITERATION)[1:4] == [
        "c0@0 <- root []",
        "c0@1 <- c0@0 []",
        "c0@2 <- c0@1 []",
    ]
    assert describe(GAP_STRIDE)[1:3] == ["c0@0 <- root []", "c0@2 <- c0@0 []"]


def test_final_step() -> None:
    assert describe(FINAL_FINISHED)[-2:] == [
        "c0@3 <- c0@2 [v@3]",
        "c0:exit extendable <- c0@3 []",
    ]
    assert describe(FINAL_OPEN)[-1] == "c0:exit extendable <- c0@2 [v@3]"
    assert describe(FINAL_FINISHED_TICK_RAISED)[-2:] == [
        "c0@3 <- c0@2 [v@3]",
        "c0:exit torn <- c0@3 null",
    ]
    assert describe(FINAL_OPEN_BODY_RAISED)[-1] == "c0:exit torn <- c0@2 null"
    assert "c1 starts from c0@3" in describe(FINAL_TICKED_FINISHED)
    assert "c1 starts from c0@2" in describe(FINAL_TICKED_OPEN)


def test_exitless_step() -> None:
    assert describe(EXITLESS_OPEN)[-1] == "c0@2 <- c0@1 [v@2]"
    assert describe(EXITLESS_FINISHED)[-1] == "c0@3 <- c0@2 [v@3]"


def test_resume_family() -> None:
    assert "c1@1 <- c0@0 [v@1]" in describe(ORIGIN_IN_LOG)
    assert "c1@1 <- root [v@1]" in describe(ORIGIN_OUTSIDE)
    assert "c1@4 <- c0@3 [v@4]" in describe(OFFSET_PREVIOUS)
    assert describe(OFFSET_EARLIER_RAN)[-3:-1] == [
        "c1@3 <- c0@2 []",
        "c1@4 <- c1@3 [v@4]",
    ]
    assert "c1@4 <- c0@2 [v@4]" in describe(OFFSET_EARLIER_SKIPPED)
    assert "c2 starts from c1@3" in describe(CLAIM_NEWER)
    assert "c2 starts from c0@3" in describe(CLAIM_OLDER)
    assert "c2 starts from c1@5" in describe(CLAIM_RUN_ON_NEWER)
    assert "c2 starts from c0@5" in describe(CLAIM_RUN_ON_OLDER)
    assert "c2 starts from c1@5" in describe(CLAIM_RUN_ON_SAVER)
    assert "c2 starts from c0@5" in describe(CLAIM_RUN_ON_SOURCE)
    assert "c1 starts from c0@3" in describe(COMMITLESS_NEWEST)
    assert "c1 starts from c0@1" in describe(COMMITLESS_ONE_FILE)
    assert "c1 starts from root" in describe(COMMITLESS_NOTHING)


def test_later_completed() -> None:
    """The golden's two readings, and §2's third: a release of the live claim."""
    assert describe(LATER_COMPLETED_FINISHED)[-3:] == [
        "withdraw x0",
        "c0@4 <- c0@3 [v@4]",
        "c0:exit closed <- c0@4 []",  # x0 was withdrawn before the worker drained it
    ]
    assert describe(LATER_COMPLETED_YIELDED)[-2:] == [
        "withdraw x0",
        "c0:exit closed <- c0@3 [v@4]",
    ]
    assert describe(LATER_COMPLETED_RELEASED)[-3:] == [
        "c0@3 <- c0@2 [v@3]",
        "c0 died lost",
        "withdraw x0",
    ]


def test_beaten_past() -> None:
    assert describe(BEATEN_PAST_NOTE)[-2:] == [
        "c0@3 <- c0@2 [v@3]",
        "c0:exit closed <- c0@3 []",
    ]
    # a mistaken release fences the live worker: its later work is no node (§4)
    assert describe(BEATEN_PAST_RELEASED)[-2:] == [
        "c0@2 <- c0@1 [v@2]",
        "c0 died errored",
    ]


def test_stopped_after_its_reap() -> None:
    assert describe(STOPPED_AFTER_ITS_REAP)[-4:] == [
        "c0@2 <- c0@1 [v@2]",
        "terminated L0 Killed(signal=9)",
        "c0 died killed",
        "withdraw x0",
    ]
    assert "c0@3 <- c0@2 [v@3]" in describe(STOPPED_AFTER_ITS_REAP_FINISHED)


def test_successor_proved_dead() -> None:
    assert describe(SUCCESSOR_PROVED_DEAD) == [
        "c0 starts from root",
        "c0@0 <- root []",
        "c0@1 <- c0@0 []",
        "c0 died lost",
        "c1 starts from c0@1",
        "c1@2 <- c0@1 []",
        "c1:exit closed <- c1@2 []",
    ]


def test_third_place_moves_past() -> None:
    assert describe(THIRD_PLACE_MOVES_PAST) == [
        "c0 starts from root",
        "c0@0 <- root [v@0]",
        "c0@1 <- c0@0 [v@1]",
        "c0@2 <- c0@1 [v@2]",
        "c0@3 <- c0@2 [v@3]",
        "c0@4 <- c0@3 [v@4]",
        "c0@5 <- c0@4 []",
        "c0 died lost",
        "c1 starts from c0@4",
        "c1@5 <- c0@4 [v@5]",
        "c1@6 <- c1@5 []",
        "c1@7 <- c1@6 [v@7]",
        "c1:exit torn <- c1@7 null",
        "c2 starts from c1@5",
        "c2@6 <- c1@5 [v@6]",
        "c2 died lost",
        "c3 starts from c1@7",
        "c3@8 <- c1@7 [v@8]",
        "c3:exit closed <- c3@8 []",
    ]


def test_disagreements_with_section_7() -> None:
    """§2's truths that §7's rules cannot give (the task report)."""
    # the successor resumed from a commit whose beat follows its claim
    assert "c1 starts from c0@2" in describe(DISPLACED_SAVED_FIRST)
    # the successor resumed from an extendable exit, its post-loop value included
    assert describe(POST_EXIT_SAVE)[4:7] == [
        "c0:exit extendable <- c0@2 [v@2]",
        "c1 starts from c0:exit",
        "c1@3 <- c0:exit [v@3]",
    ]


# ----- the rest of the type ----------------------------------------------------------


def test_a_rewind_restates_the_position() -> None:
    """An in-claim rewind (§7, M9 refuses one: a heartbeat at or below a step
    its claim already beat) is a native ``lifecycle.rewind``; the pending values
    it drops stay outside every lineage."""
    h = _h(
        Claim(0, Fresh(), None),
        run(0, range(3), SAVES),
        Emit(0, VALUE, stepless=False),
        Rewind(0, CommitOf(0, 0)),
        run(0, [1], SAVES),
        _completes(0),
    )
    assert describe(h)[3:] == [
        "c0@2 <- c0@1 [v@2]",
        "c0 rewinds to c0@0",
        "c0@1 <- c0@0 [v@1]",
        "c0:exit closed <- c0@1 []",
    ]
    assert violations(truth(h)) == ()


def test_stepless_records_are_liveness_and_registers() -> None:
    """A stepless heartbeat is liveness, never a node, and a stepless value is
    a register, never committed (§7, M3)."""
    h = _h(
        Claim(0, Fresh(), None),
        Emit(0, "status", stepless=True),
        Beacon(0),
        run(0, [0], SAVES),
        _completes(0),
    )
    heartbeat = render(h).rows[2]
    assert json.loads(heartbeat.body)["step"] is None
    assert describe(h)[1:] == ["c0@0 <- root [v@0]", "c0:exit closed <- c0@0 []"]


# ----- what the model excludes -------------------------------------------------------


@pytest.mark.parametrize(
    "events, rule",
    [
        (  # a report opens 0.3.0's gate over a live worker, never 0.4.0's
            [
                Claim(0, Fresh(), None),
                ThirdPartyStop(0, Report(None), None, NULL, Honors.NONE),
                Claim(1, Fresh(), None),
            ],
            "held by a live worker",
        ),
        (  # a resume loads a save made before its claim (§6)
            [
                Claim(0, Fresh(), None),
                Begin(0, 0),
                _released(0, Outcome.LOST),
                Claim(1, FromSave(CommitOf(0, 0)), None),
            ],
            "no save of",
        ),
        (  # a worker's stopped is its halt
            [Claim(0, Fresh(), None), Exit(0, False, None, YIELDED), Beat(0)],
            "a beat ticks an iteration",
        ),
        (  # completed carries no error
            [Claim(0, Fresh(), None), Exit(0, True, "boom", YIELDED)],
            "carries no error",
        ),
        (  # run-absolute steps: a resumed claim's labels rise above its state
            [
                Claim(0, Fresh(), None),
                *run(0, [0, 1], SAVES),
                _killed(0),
                Claim(1, FromSave(CommitOf(0, 1)), None),
                Begin(1, 1),
            ],
            "below the clock's floor",
        ),
        (  # the run is at rest
            [Claim(0, Fresh(), None)],
            "never halts",
        ),
    ],
)
def test_the_model_refuses_what_no_conforming_writer_does(
    events: list[Event], rule: str
) -> None:
    with pytest.raises(NotConforming, match=rule):
        walk(History(tuple(events)))


def test_a_released_workers_later_save_never_lands() -> None:
    """A released worker fences itself (§4), so nothing loads what it saved
    after its release."""
    released = [
        Claim(0, Fresh(), None),
        *run(0, [0], SAVES),
        _released(0, Outcome.LOST),
        *run(0, [1], SAVES),
        _killed(0),
    ]
    with pytest.raises(NotConforming, match="no save of"):
        walk(_h(released, Claim(1, FromSave(CommitOf(0, 1)), None), _killed(1)))
    walk(_h(released, Claim(1, FromSave(CommitOf(0, 0)), None), _killed(1)))
