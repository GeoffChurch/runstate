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
   name, every golden §7 lists that a conforming writer can write, and the
   d78b72f1 shape, are conforming histories. Each golden's history renders the
   golden's log, as ``tests/test_migration_v0_3_0_to_v0_4_0.py`` builds it; the
   histories an entry says share a log do share it; and each truth is what
   §2 gives for its history, which the tests state line by line.

Every enumeration stays under 2,000 cases and streams.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple

import pytest

import tests.migration_sim.enumerate as enumeration
import tests.test_migration_v0_3_0_to_v0_4_0 as goldens
from runstate.channel import Channel, create_channel
from runstate.migrations import Row
from runstate.worker import Worker
from tests.migration_sim.build import (
    VALUE,
    Cut,
    Saving,
    Shape,
    Step,
    Tick,
    iteration,
    own_process,
    run,
)
from tests.migration_sim.enumerate import (
    COMPLETES,
    MYCOOC,
    DISPLACED_SAVES,
    DISPLACEMENT,
    END_TIMES,
    KILL,
    LOADING,
    MID_SAVES,
    POSITIONAL,
    PREEMPTED,
    RELEASED_LIVE,
    RESUMES,
    SAVE_POINTS,
    STOPS_AND_REAPS,
    THIRD_PARTY_ACTS,
    THIRD_PARTIES,
    UNANSWERED_STOPS,
    WITHDRAWALS,
    WORKER_SHAPES,
    LaunchShape,
    MidPosition,
    Origins,
    Space,
    StopSlot,
    Strays,
    Successor,
    ended_stops,
    histories,
    rewinds,
    slot,
)
from tests.migration_sim.history import (
    Beacon,
    Beat,
    Begin,
    Born,
    Claim,
    Clock,
    Drain,
    Emit,
    CutSave,
    TICKED,
    Undated,
    Event,
    Exit,
    Exited,
    ExitSave,
    FinalStep,
    Finish,
    Fold,
    FromOutside,
    FromSave,
    Fresh,
    Gate,
    Halt,
    History,
    Honors,
    Killed,
    Launched,
    NotConforming,
    Origin,
    Outcome,
    PassSave,
    Process,
    Reaped,
    Release,
    Report,
    Rewind,
    Save,
    SaveOf,
    Stamp,
    StopRequest,
    Stray,
    ThirdPartyFinal,
    ThirdPartyStop,
    ThirdPartyValue,
    UserRecord,
    Via,
    Watermark,
    conforms,
    loadable,
    loads_newest,
    walk,
)
from tests.migration_sim.render import Rendered, records, render
from tests.migration_sim.truth import (
    Case,
    Commit,
    Source,
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
    "end-times": Family(1, 2, 0, END_TIMES),
    "mid-saves": Family(2, 1, 0, MID_SAVES),
    "resumes": Family(2, 2, 0, RESUMES),
    "loading": Family(2, 1, 0, LOADING),
    "third-parties": Family(2, 1, 0, THIRD_PARTIES),
    "released-live": Family(1, 2, 0, RELEASED_LIVE),
    "displacement": Family(2, 2, 0, DISPLACEMENT),
    "displaced-saves": Family(3, 1, 0, DISPLACED_SAVES),
    "stops-and-reaps": Family(2, 1, 1, STOPS_AND_REAPS),
    "unanswered-stops": Family(2, 1, 1, UNANSWERED_STOPS),
    "withdrawals": Family(1, 2, 1, WITHDRAWALS),
    "save-points": Family(2, 1, 0, SAVE_POINTS),
    "third-party-acts": Family(2, 1, 0, THIRD_PARTY_ACTS),
    "positional": Family(2, 1, 1, POSITIONAL),
}
BOUND = 2000


@pytest.mark.parametrize("family", FAMILIES.values(), ids=list(FAMILIES))
def test_enumeration_is_bounded(family: Family) -> None:
    """Under the bound, and each history once, so a count is of distinct
    histories."""
    hs = list(_histories(family))
    assert len(hs) < BOUND and len(set(hs)) == len(hs)


def test_enumeration_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first history arrives after one conformance check, not after the
    family is built."""
    checks = 0
    real = conforms

    def counting(history: History) -> bool:
        nonlocal checks
        checks += 1
        return real(history)

    monkeypatch.setattr(enumeration, "conforms", counting)
    stream = _histories(FAMILIES["worker-shapes"])
    next(stream)
    assert checks == 1
    yielded = 1 + sum(1 for _ in stream)
    assert yielded > 900 and checks >= yielded


@pytest.mark.parametrize("family", FAMILIES.values(), ids=list(FAMILIES))
def test_families_share_logs_between_truths(family: Family) -> None:
    """Each family generates an ambiguous log: two histories that write it and
    need different outputs. ``withdrawals``' are each a pair that differ only in
    where an iteration no record dates ended against the stop: whether the
    commit's drain read it, and so whether the native exit honours it."""
    lineages = defaultdict(set)
    for h in _histories(family):
        lineages[render(h).rows].add(truth(h).lineage)
    assert any(len(ls) > 1 for ls in lineages.values())


def _cost_counts() -> dict[str, Counter[Case]]:
    return {
        name: Counter(c.case for h in _histories(f) for c in truth(h).costs)
        for name, f in FAMILIES.items()
    }


def test_every_difference_is_a_stated_cost() -> None:
    """E2 (§7): where a native exit's ``honored`` differs from its 0.3.0 list,
    the difference is one of the five cases of the cost the owner accepted, and
    the truth raises ``Residue`` for any other, so every family's truth is one
    of them. The families reach each case: ``unanswered-stops`` the ticks-only
    exit (1) and a later exit omitting what it discharged (4), ``withdrawals``
    a stop cleared after a commit's drain read it (2) and the at-claim writer's
    stop answered after its claim (3), and ``positional`` an exit whose input
    list the earlier step inferred (5), which only a log born in 0.2.0 has."""
    counts = _cost_counts()
    assert counts["unanswered-stops"][Case.TICKS_EXIT] > 0
    assert counts["unanswered-stops"][Case.DISCHARGED_EARLIER] > 0
    assert counts["withdrawals"][Case.CLEARED_AFTER_DRAIN] > 0
    assert counts["withdrawals"][Case.ANSWERED_AFTER_CLAIM] > 0
    assert counts["positional"][Case.INFERRED_INPUT_LIST] > 0
    born_0_3_0 = [
        name for name, f in FAMILIES.items() if f.space.born == (Born.V0_3_0,)
    ]
    assert not any(counts[name][Case.INFERRED_INPUT_LIST] for name in born_0_3_0)


def test_released_workers_work_after_their_release() -> None:
    """A fenced worker finishes, beats and saves in enumeration, so the
    release-or-note and fence-or-race ambiguities (D1, D2) occur there."""
    fenced: set[str] = set()
    for family in (FAMILIES["released-live"], FAMILIES["displacement"]):
        for h in _histories(family):
            for ev, f in zip(h.events, render(h).walk.fenced):
                if f:
                    fenced.add(type(ev).__name__)
    assert {"Begin", "Emit", "Finish", "Beat", "Exit", "Halt"} <= fenced


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
            for prefix, convention in conventions.items():
                if record["topic"].startswith(prefix):
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


# ----- builders for named histories --------------------------------------------------

YIELDED = FinalStep.YIELDED
NULL, PROGRESS = ThirdPartyFinal.NULL, ThirdPartyFinal.PROGRESS
CONTROL, TAIL = Watermark.CONTROL, Watermark.TAIL
SAVES = Shape(
    True,
    Tick.AFTER_BODY,
    CONTROL,
    Saving.IN_BODY,
    trailing=False,
    ended=Undated.OWN_LAST,
)
"""The ``steps()`` worker that emits each iteration and saves in its body."""
BEATS = Shape(
    False,
    Tick.AFTER_BODY,
    CONTROL,
    Saving.IN_BODY,
    trailing=False,
    ended=Undated.OWN_LAST,
)
FIRST = Shape(
    False,
    Tick.BEFORE_BODY,
    CONTROL,
    Saving.IN_BODY,
    trailing=False,
    ended=Undated.OWN_LAST,
)
UNLOGGED = Shape(
    False, Tick.NONE, CONTROL, Saving.IN_BODY, trailing=False, ended=Undated.OWN_LAST
)
QUIET = Shape(
    False, Tick.NONE, CONTROL, Saving.NONE, trailing=False, ended=Undated.OWN_LAST
)
UNSAVED = Shape(
    True, Tick.AFTER_BODY, CONTROL, Saving.NONE, trailing=False, ended=Undated.OWN_LAST
)
BACKSTOP = goldens.BACKSTOP


def _h(*parts: Event | list[Event]) -> History:
    events: list[Event] = []
    for part in parts:
        events += part if isinstance(part, list) else [part]
    return History(tuple(events), Born.V0_3_0)


def _claim(claim: int, origin: Origin) -> Claim:
    return Claim(
        claim,
        own_process(claim),
        Gate.FREE,
        origin,
        None,
        Clock.LOADED,
        Fold.AT_CLAIM,
        CutSave.LANDED,
    )


def _takeover(claim: int, origin: Origin) -> Claim:
    return Claim(
        claim,
        own_process(claim),
        Gate.TAKEOVER,
        origin,
        None,
        Clock.LOADED,
        Fold.AT_CLAIM,
        CutSave.LANDED,
    )


def _exit(claim: int, *, completed: bool, error: str | None, final: FinalStep) -> Exit:
    return Exit(claim, completed, error, final, Drain.EXIT, discards=False)


def _killed(claim: int) -> Halt:
    return Halt(claim, Killed(9))


def _completes(claim: int) -> list[Event]:
    return [
        _exit(claim, completed=True, error=None, final=YIELDED),
        Halt(claim, Exited(0)),
    ]


def _preempted(claim: int) -> list[Event]:
    return [
        _exit(claim, completed=False, error=None, final=YIELDED),
        Halt(claim, Exited(0)),
    ]


def _released(claim: int, outcome: Outcome) -> ThirdPartyStop:
    return ThirdPartyStop(claim, Release(outcome, Via.HELPER), None, NULL, Honors.NONE)


def _emits(claim: int, names: tuple[str, ...]) -> list[Event]:
    return [Emit(claim, name, Stamp.METRIC) for name in names]


def _open(claim: int, step: int, *names: str) -> list[Event]:
    """An iteration begun, its values written."""
    return [Begin(claim, step), *_emits(claim, names)]


def _finished(claim: int, step: int, *names: str) -> list[Event]:
    """An iteration whose pass closed with its save as its last act, its tick
    skipped: it ended with its own newest record."""
    return _open(claim, step, *names) + [Finish(claim, True, Undated.OWN_LAST)]


def _ticked(
    claim: int, step: int, names: tuple[str, ...], mark: Watermark
) -> list[Event]:
    """An iteration whose pass closed with its save, then its tick, which dates
    its end."""
    return _open(claim, step, *names) + [
        Finish(claim, True, TICKED),
        Beat(claim, mark),
    ]


def _after(claim: int, step: int, *names: str) -> list[Event]:
    """A steps() iteration that saves in its pass: values, then its tick."""
    return _ticked(claim, step, names, CONTROL)


def _after_claimed(claim: int, step: int, *names: str) -> list[Event]:
    """As ``_after``, its beat's cursor starting at its claim."""
    return _ticked(claim, step, names, Watermark.CLAIMED)


def _after_tail(claim: int, step: int, *names: str) -> list[Event]:
    """As ``_after``, its beat's watermark the log's tail."""
    return _ticked(claim, step, names, TAIL)


def _first(claim: int, step: int, *names: str) -> list[Event]:
    """A tick-first iteration that saves in its pass."""
    return [
        Begin(claim, step),
        Beat(claim, CONTROL),
        *_emits(claim, names),
        Finish(claim, True, Undated.OWN_LAST),
    ]


# ----- 3. the reference Worker -------------------------------------------------------

WORKER = Space(
    origins=(Origins.FRESH, Origins.SAVED),
    loading=(False,),
    clocks=(Clock.LOADED,),
    folds=(Fold.AT_CLAIM,),
    cut_saves=(CutSave.LANDED,),
    strays=(Strays.NONE,),
    pre_saves=(False,),
    strides=(1,),
    shapes=(SAVES,),
    mid_claim=(None,),
    mid_positions=(MidPosition.BEFORE_BODY,),
    cuts=(Cut.MID_BODY, Cut.DONE),
    endings=(KILL, COMPLETES, PREEMPTED),
    third_parties=(None,),
    successors=(Successor.OWN_PROCESS, Successor.SAME_PROCESS),
    displaced=(),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(StopSlot.BEFORE_CLAIM,),
    born=(Born.V0_3_0,),
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
    """Two Workers run in turn by this one process, as a resume in one process
    is: they share its handle."""
    with Worker(ch) as w:  # its __exit__ writes the preempted exit
        for s in w.steps(2):
            w.emit(VALUE, f"{VALUE}@{s}")
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
    log: Sequence[dict[str, Any]], *, payloads: bool
) -> list[tuple[str, str | None, str | None, str]]:
    """The log with every ``t`` dropped, handles and request ids renamed by
    first appearance, and, unless ``payloads``, values' payloads and the bodies
    of topics no convention reserves dropped."""
    ids: dict[str, str] = {}

    def rename(x: str) -> str:
        return ids.setdefault(x, f"id{len(ids)}")

    out = []
    for r in log:
        topic = r["topic"]
        body = {k: v for k, v in r["body"].items() if k != "t"}
        if not payloads and topic == "value":
            del body["value"]
        elif not payloads and not topic.startswith(
            ("lifecycle.", "control.", "launcher.")
        ):
            body = {}
        if "handle" in body:
            body["handle"] = rename(body["handle"])
        if "honored" in body:
            body["honored"] = [rename(x) for x in body["honored"]]
        rid = None if r["request_id"] is None else rename(r["request_id"])
        out.append((str(topic), r["name"], rid, json.dumps(body, sort_keys=True)))
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
        ],
        payloads=True,
    )
    n = 0
    for h in _histories(scenario.family):
        n += 1
        if _normalised(records(render(h)), payloads=True) == worker_log:
            break
    else:
        pytest.fail(f"no history of {n} renders the Worker's log {worker_log}")
    assert n < BOUND


# ----- 4. the named histories: §7's ambiguity entries ---------------------------------

# stop-author: H1 the Worker's chunk-cap exit honouring x; H2 a tool's same body
# for a claim killed with its node, honouring the pending x.
STOP_AUTHOR_EXIT = _h(
    _claim(0, Fresh()),
    run(0, range(3), SAVES),
    StopRequest("x0"),
    _preempted(0),
)
STOP_AUTHOR_THIRD_PARTY = _h(
    _claim(0, Fresh()),
    run(0, range(3), SAVES),
    _killed(0),
    StopRequest("x0"),
    ThirdPartyStop(
        0, Release(Outcome.LOST, Via.HELPER), None, PROGRESS, Honors.UNDISCHARGED
    ),
)


# third-party-stop. Its H1 (after the worker's exit) and H2 (a dead claim) are
# different logs as written, so each is paired with a same-log counterpart.
def _after_exit(intent: Release | Report, error: str | None) -> History:
    return _h(
        _claim(0, Fresh()),
        run(0, range(2), SAVES),
        _preempted(0),
        ThirdPartyStop(0, intent, error, NULL, Honors.NONE),
    )


def _after_death(intent: Release | Report) -> History:
    return _h(
        _claim(0, Fresh()),
        run(0, range(2), SAVES),
        _killed(0),
        ThirdPartyStop(0, intent, None, NULL, Honors.NONE),
    )


TPS_CLEARS_STAGED = _h(
    _claim(0, Fresh()),
    run(0, range(2), SAVES),
    _preempted(0),
    StopRequest("x0"),
    ThirdPartyStop(0, Report(None), None, NULL, Honors.UNDISCHARGED),
)
TPS_NOTE_AFTER_EXIT = _after_exit(Report(None), None)
TPS_LOSER_REAPED_AFTER_EXIT = _after_exit(Report(Exited(1)), None)
TPS_RELEASE_AFTER_EXIT = _after_exit(Release(Outcome.LOST, Via.HELPER), None)
TPS_CAUSE_AFTER_EXIT = _after_exit(
    Release(Outcome.ERRORED, Via.HELPER), "slurm: NODE_FAIL"
)
TPS_NOTE_WITH_ERROR_AFTER_EXIT = _after_exit(Report(None), "slurm: NODE_FAIL")
TPS_RELEASE_OF_DEAD = _after_death(Release(Outcome.LOST, Via.HELPER))
TPS_NOTE_ON_DEAD = _after_death(Report(None))

# value-claim: H1 the successor's own value; H2 a released worker's late value,
# and H2' an exited worker's teardown value, after the successor claimed; and
# the takeover the site excludes, a successor over a live worker no stopped
# named.
_VC_FIRST = [_claim(0, Fresh()), *run(0, range(2), SAVES), Begin(0, 2)]
_VC_RESUMES = FromSave(SaveOf(0, 1))
VALUE_CLAIM_SUCCESSORS = _h(
    _VC_FIRST,
    _killed(0),
    _released(0, Outcome.LOST),
    _claim(1, _VC_RESUMES),
    iteration(1, Step(2, SAVES), cut=Cut.DONE),
    _completes(1),
)
VALUE_CLAIM_DISPLACED = _h(
    _VC_FIRST,
    _released(0, Outcome.LOST),
    _claim(1, _VC_RESUMES),
    Emit(0, VALUE, Stamp.METRIC),
    _killed(0),
    iteration(1, Step(2, BEATS), cut=Cut.DONE),
    _completes(1),
)
VALUE_CLAIM_EXITED_SUCCESSORS = _h(
    _VC_FIRST,
    _preempted(0),
    _claim(1, _VC_RESUMES),
    iteration(1, Step(2, SAVES), cut=Cut.DONE),
    _completes(1),
)
VALUE_CLAIM_TEARDOWN = _h(
    _VC_FIRST,
    _exit(0, completed=False, error=None, final=YIELDED),
    _claim(1, _VC_RESUMES),
    Emit(0, VALUE, Stamp.METRIC),
    Halt(0, Exited(0)),
    iteration(1, Step(2, BEATS), cut=Cut.DONE),
    _completes(1),
)
VALUE_CLAIM_DEAD_UNNAMED = _h(
    _VC_FIRST,
    _killed(0),
    _claim(1, _VC_RESUMES),
    iteration(1, Step(2, SAVES), cut=Cut.DONE),
    _completes(1),
)
VALUE_CLAIM_TAKEN_OVER = _h(
    _VC_FIRST,
    _takeover(1, _VC_RESUMES),
    Emit(0, VALUE, Stamp.METRIC),
    _killed(0),
    iteration(1, Step(2, BEATS), cut=Cut.DONE),
    _completes(1),
)

# beat-ends-iteration: H1 steps() killed in step 3's body; H2 a tick-first loop
# killed in step 2's body.
BEAT_ENDS = _h(_claim(0, Fresh()), run(0, range(3), BEATS), Begin(0, 3), _killed(0))
BEAT_STARTS = _h(
    _claim(0, Fresh()),
    run(0, range(2), FIRST),
    Begin(0, 2),
    Beat(0, CONTROL),
    _killed(0),
)

# post-beat-values: H1 a tick-first loop's metrics after tick(2); H2 a steps()
# worker's post-loop value.
POST_BEAT_LABEL = _h(
    _claim(0, Fresh()),
    run(0, range(2), FIRST),
    _first(0, 2, VALUE),
    _preempted(0),
)
POST_BEAT_EXIT_HOLDS = _h(
    _claim(0, Fresh()),
    run(0, range(3), BEATS),
    Emit(0, VALUE, Stamp.METRIC),
    _preempted(0),
)

# leading-values: H1 a loading status at the resume step 4; H2 an unticked step 4
# after a resume from 3.
_LV_BASE = [_claim(0, Fresh()), *run(0, range(5), SAVES), _killed(0)]
LEADING_LOADING = _h(
    _LV_BASE,
    _claim(1, FromSave(SaveOf(0, 4))),
    Emit(1, VALUE, Stamp.METRIC),
    run(1, [5], BEATS),
    _completes(1),
)
LEADING_UNTICKED = _h(
    _LV_BASE,
    _claim(1, FromSave(SaveOf(0, 3))),
    _finished(1, 4, VALUE),
    run(1, [5], BEATS),
    _completes(1),
)

# beatless-claim: H1 a claim that crashed while loading, its status at 3; H2 one
# that finished step 3 and saved it, and the next claim started at 4.
_BC_BASE = [_claim(0, Fresh()), *run(0, range(4), SAVES), _killed(0)]


def _bc_last(origin: SaveOf) -> list[Event]:
    return [_claim(2, FromSave(origin)), *run(2, [4], SAVES), *_completes(2)]


BEATLESS_LOADING = _h(
    _BC_BASE,
    _claim(1, FromSave(SaveOf(0, 3))),
    Emit(1, VALUE, Stamp.METRIC),
    _killed(1),
    _bc_last(SaveOf(0, 3)),
)
BEATLESS_FINISHED = _h(
    _BC_BASE,
    _claim(1, FromSave(SaveOf(0, 2))),
    _finished(1, 3, VALUE),
    _killed(1),
    _bc_last(SaveOf(1, 3)),
)


# d78b72f1: claim 1 beats 0-180, writes 181 and is killed while saving; a beatless
# claim resumes at 182 and crashes saving; a beatless claim resumes at 183 and
# exits completed with final_step 183. The run's later resumes say that 181 and
# 182 finished (§7, beatless-claim H2); D78_181_OPEN is the same log where 181
# did not, and the second claim ran 181 unlogged.
def _d78(first: list[Event], second: list[Event]) -> History:
    return _h(
        _claim(0, Fresh()),
        run(0, range(181), SAVES),
        first,
        _killed(0),
        second,
        _finished(1, 182, VALUE),
        _exit(1, completed=False, error="crashed while saving", final=FinalStep.TICKED),
        Halt(1, Exited(1)),
        _claim(2, FromSave(SaveOf(1, 182))),  # claim 1's save of 182
        _finished(2, 183, VALUE),
        _exit(2, completed=True, error=None, final=FinalStep.FINISHED),
        Halt(2, Exited(0)),
    )


D78 = _d78(_finished(0, 181, VALUE), [_claim(1, FromSave(SaveOf(0, 181)))])
D78_181_OPEN = _d78(
    _open(0, 181, VALUE),
    [
        _claim(1, FromSave(SaveOf(0, 180))),
        Begin(1, 181),
        Finish(1, False, Undated.OWN_LAST),
    ],
)

# valueless-gap: H1 an unticked, valueless iteration; H2 a stride.
GAP_ITERATION = _h(
    _claim(0, Fresh()),
    run(0, [0], BEATS),
    run(0, [1], UNLOGGED),
    run(0, [2], BEATS),
    _completes(0),
)
GAP_STRIDE = _h(_claim(0, Fresh()), run(0, [0, 2], BEATS), _completes(0))


# final-step: H1 the worker finished its final_step; H2 it had not, its native
# counterpart keeping the open iteration (extendable) or discarding it (torn).
# Clean exits, error exits, and a manual-tick worker's final_step.
def _final(finished: bool, error: str | None, discards: bool) -> History:
    return _h(
        _claim(0, Fresh()),
        run(0, range(3), SAVES),
        _open(0, 3, VALUE),
        [Finish(0, True, Undated.OWN_LAST)] if finished else [],
        Exit(0, False, error, YIELDED, Drain.EXIT, discards),
        Halt(0, Exited(0 if error is None else 1)),
    )


def _final_ticked(finished: bool) -> History:
    return _h(
        _claim(0, Fresh()),
        run(0, range(3), SAVES),
        _open(0, 3, VALUE),
        [Finish(0, True, Undated.OWN_LAST)] if finished else [],
        _exit(0, completed=False, error="crashed", final=FinalStep.TICKED),
        Halt(0, Exited(1)),
        _claim(1, FromSave(SaveOf(0, 3 if finished else 2))),
        run(1, [4], SAVES),
        _completes(1),
    )


FINAL_FINISHED = _final(True, None, discards=False)
FINAL_OPEN = _final(False, None, discards=False)
FINAL_OPEN_DISCARDED = _final(False, None, discards=True)
FINAL_FINISHED_TICK_RAISED = _final(True, "boom", discards=False)
FINAL_OPEN_BODY_RAISED = _final(False, "boom", discards=False)
FINAL_TICKED_FINISHED = _final_ticked(True)
FINAL_TICKED_OPEN = _final_ticked(False)

# exitless-step: H1 killed inside step 3's body; H2 finished and saved step 3,
# killed before its tick.
EXITLESS_OPEN = _h(
    _claim(0, Fresh()), run(0, range(3), SAVES), _open(0, 3, VALUE), _killed(0)
)
EXITLESS_FINISHED = _h(
    _claim(0, Fresh()), run(0, range(3), SAVES), _finished(0, 3, VALUE), _killed(0)
)


# resume-origin: C2 loaded C1's save at 0 (H1), or a checkpoint from outside (H2).
def _origin(origin: Origin) -> History:
    return _h(
        _claim(0, Fresh()),
        run(0, [0], SAVES),
        _killed(0),
        _claim(1, origin),
        run(1, [1, 2], SAVES),
        _completes(1),
    )


ORIGIN_IN_LOG = _origin(FromSave(SaveOf(0, 0)))
ORIGIN_OUTSIDE = _origin(FromOutside(0))

# resume-offset: C1 saved every step and C2 loaded 3 (H1); C1 saved every second
# step, and C2 loaded 2 and ran 3 unlogged, or skipped it.
_EVERY = [_claim(0, Fresh()), *run(0, range(4), SAVES), _killed(0)]
_ALTERNATE = [
    _claim(0, Fresh()),
    *run(0, [0], SAVES),
    *run(0, [1], UNSAVED),
    *run(0, [2], SAVES),
    *run(0, [3], UNSAVED),
    _killed(0),
]
OFFSET_PREVIOUS = _h(
    _EVERY, _claim(1, FromSave(SaveOf(0, 3))), run(1, [4], SAVES), _completes(1)
)
OFFSET_EARLIER_RAN = _h(
    _ALTERNATE,
    _claim(1, FromSave(SaveOf(0, 2))),
    run(1, [3], UNLOGGED),
    run(1, [4], SAVES),
    _completes(1),
)
OFFSET_EARLIER_SKIPPED = _h(
    _ALTERNATE, _claim(1, FromSave(SaveOf(0, 2))), run(1, [4], SAVES), _completes(1)
)

# resume-claim: H1/H2 both earlier claims beat 3; H3 two run-ons to 5; H4 a
# run-on to 5 beside a source of 5.
_RC_BASE = [
    _claim(0, Fresh()),
    *run(0, range(4), SAVES),
    _killed(0),
    _claim(1, FromSave(SaveOf(0, 2))),
    *run(1, [3], SAVES),
    _killed(1),
]
_RC_H3 = [
    _claim(0, Fresh()),
    *run(0, range(4), BEATS),
    *run(0, [4], QUIET),
    *run(0, [5], UNLOGGED),
    _killed(0),
    _claim(1, Fresh()),
    *run(1, range(2), BEATS),
    *run(1, [2, 3, 4], QUIET),
    *run(1, [5], UNLOGGED),
    _killed(1),
]
_RC_H4 = [
    _claim(0, Fresh()),
    *run(0, range(6), BEATS),
    _killed(0),
    _claim(1, Fresh()),
    *run(1, range(4), BEATS),
    *run(1, [4], QUIET),
    *run(1, [5], UNLOGGED),
    _killed(1),
]


def _third_from(base: list[Event], origin: SaveOf, step: int) -> History:
    return _h(base, _claim(2, FromSave(origin)), run(2, [step], SAVES), _completes(2))


CLAIM_NEWER = _third_from(_RC_BASE, SaveOf(1, 3), 4)
CLAIM_OLDER = _third_from(_RC_BASE, SaveOf(0, 3), 4)
CLAIM_RUN_ON_NEWER = _third_from(_RC_H3, SaveOf(1, 5), 6)
CLAIM_RUN_ON_OLDER = _third_from(_RC_H3, SaveOf(0, 5), 6)
CLAIM_RUN_ON_SAVER = _third_from(_RC_H4, SaveOf(1, 5), 6)
CLAIM_RUN_ON_SOURCE = _third_from(_RC_H4, SaveOf(0, 5), 6)


# commitless-resume: J loaded A's newest save (H1), or the one file that held A's
# save at 1, or nothing (H2); B then resumed from A's 1.
def _commitless(origin: Origin) -> History:
    return _h(
        _claim(0, Fresh()),
        run(0, range(4), SAVES),
        _killed(0),
        _claim(1, origin),
        _killed(1),
        _claim(2, FromSave(SaveOf(0, 1))),
        run(2, [2], SAVES),
        _completes(2),
    )


COMMITLESS_NEWEST = _commitless(FromSave(SaveOf(0, 3)))
COMMITLESS_ONE_FILE = _commitless(FromSave(SaveOf(0, 1)))
COMMITLESS_NOTHING = _commitless(Fresh())


# LATER_COMPLETED: beats 0-3; stop x; a default-body stop honouring x; a value at
# 4; a completed stop. The third party cleared x (a report) or, mistakenly,
# released the live claim.
def _later_completed(intent: Release | Report, finished: bool) -> History:
    return _h(
        _claim(0, Fresh()),
        run(0, range(4), SAVES),
        StopRequest("x0"),
        ThirdPartyStop(0, intent, None, NULL, Honors.UNDISCHARGED),
        _open(0, 4, VALUE),
        [Finish(0, True, Undated.OWN_LAST)] if finished else [],
        _completes(0),
    )


LATER_COMPLETED_FINISHED = _later_completed(Report(None), finished=True)
LATER_COMPLETED_YIELDED = _later_completed(Report(None), finished=False)
LATER_COMPLETED_RELEASED = _later_completed(
    Release(Outcome.LOST, Via.HELPER), finished=True
)


# BEATEN_PAST: beats 0-2; a stopped with an error; a value at 3; a beat at 3; a
# completed stop. The error stop was a note, or a mistaken release.
def _beaten_past(intent: Release | Report) -> History:
    return _h(
        _claim(0, Fresh()),
        run(0, range(3), SAVES),
        ThirdPartyStop(0, intent, "job 7 FAILED", NULL, Honors.NONE),
        run(0, [3], SAVES),
        _completes(0),
    )


BEATEN_PAST_NOTE = _beaten_past(Report(None))
BEATEN_PAST_RELEASED = _beaten_past(Release(Outcome.ERRORED, Via.HELPER))


# STOPPED_AFTER_ITS_REAP: launched L; the claim with L's handle; values and beats
# 0-2; a value at 3; L killed by signal 9; a stop x; a default body honouring x.
def _after_reap(finished: bool) -> History:
    return _h(
        Launched("L0", own_process(0)),
        Claim(
            0,
            own_process(0),
            Gate.FREE,
            Fresh(),
            "L0",
            Clock.LOADED,
            Fold.AT_CLAIM,
            CutSave.LANDED,
        ),
        run(0, range(3), SAVES),
        (_finished if finished else _open)(0, 3, VALUE),
        _killed(0),
        Reaped("L0", Killed(9)),
        StopRequest("x0"),
        ThirdPartyStop(0, Report(None), None, NULL, Honors.UNDISCHARGED),
    )


STOPPED_AFTER_ITS_REAP = _after_reap(finished=False)
STOPPED_AFTER_ITS_REAP_FINISHED = _after_reap(finished=True)

# SUCCESSOR_PROVED_DEAD: claim 1 beats 0 and 1; claim 2 beats 2 and exits
# completed at 2. Its first claim was dead; or alive, and taken over.
SUCCESSOR_PROVED_DEAD = _h(
    _claim(0, Fresh()),
    run(0, range(2), BEATS),
    _killed(0),
    _claim(1, FromSave(SaveOf(0, 1))),
    run(1, [2], BEATS),
    _completes(1),
)
SUCCESSOR_TOOK_OVER = _h(
    _claim(0, Fresh()),
    run(0, range(2), BEATS),
    _takeover(1, FromSave(SaveOf(0, 1))),
    run(1, [2], BEATS),
    _completes(1),
    _killed(0),
)

# THIRD_PLACE_MOVES_PAST: A beats 0-4 with a value at each and writes loss@6, and
# nothing ends it. B writes loss@5 and loss@7, and an error stopped with a null
# final_step. L0 writes loss@6, beats 6, and nothing ends it. L1 writes loss@8,
# beats 8 and exits completed with final_step 8. The history its stated output
# needs: A ran 5 unlogged and was killed in 6; B resumed from A's 4 and ran 5 to
# 7, 6 unlogged; L0 resumed from B's 5, L1 from B's 7.
THIRD_PLACE_MOVES_PAST = _h(
    _claim(0, Fresh()),
    run(0, range(5), SAVES),
    run(0, [5], UNLOGGED),
    _open(0, 6, VALUE),
    _killed(0),
    _claim(1, FromSave(SaveOf(0, 4))),
    _finished(1, 5, VALUE),
    run(1, [6], UNLOGGED),
    _finished(1, 7, VALUE),
    _exit(1, completed=False, error="boom", final=FinalStep.TICKED),
    Halt(1, Exited(1)),
    _claim(2, FromSave(SaveOf(1, 5))),
    run(2, [6], SAVES),
    _killed(2),
    _claim(3, FromSave(SaveOf(1, 7))),
    run(3, [8], SAVES),
    _completes(3),
)

# Two shapes §7 does not name, where §2's truth is one §7's rules cannot give
# (the task report, "Disagreements").
# A released worker finished and saved 2 before the release; its beat at 2 lands
# after its successor claimed, and the successor resumed from that commit.
DISPLACED_SAVED_FIRST = _h(
    _claim(0, Fresh()),
    run(0, range(2), SAVES),
    _finished(0, 2, VALUE),
    _released(0, Outcome.LOST),
    _claim(1, FromSave(SaveOf(0, 2))),
    Beat(0, CONTROL),
    _killed(0),
    run(1, [3], SAVES),
    _completes(1),
)
# The same log, where 2 finished only after the release (fenced) and the
# successor resumed from 1 and ran 2 unlogged.
DISPLACED_SAVED_AFTER = _h(
    _claim(0, Fresh()),
    run(0, range(2), SAVES),
    _open(0, 2, VALUE),
    _released(0, Outcome.LOST),
    _claim(1, FromSave(SaveOf(0, 1))),
    Finish(0, True, Undated.OWN_LAST),
    Beat(0, CONTROL),
    _killed(0),
    run(1, [2], UNLOGGED),
    run(1, [3], SAVES),
    _completes(1),
)


# A synthetic writer saved after its own preempted exit; the next claim loaded
# that save, the exit node, or the last commit's. It is §6's rule (D3), not a
# mycooc order: every W1 revision force-saves its resume checkpoint before its
# stop (task-19-report.md, P-a), and its post-exit save phase writes outputs no
# resume loads.
def _post_exit_save(origin: SaveOf | ExitSave) -> History:
    return _h(
        _claim(0, Fresh()),
        run(0, range(3), SAVES),
        Emit(0, VALUE, Stamp.METRIC),
        _exit(0, completed=False, error=None, final=YIELDED),
        Save(0),
        Halt(0, Exited(0)),
        _claim(1, FromSave(origin)),
        run(1, [3], SAVES),
        _completes(1),
    )


SYNTHETIC_POST_EXIT_SAVE = _post_exit_save(ExitSave(0))
SYNTHETIC_POST_EXIT_COMMIT = _post_exit_save(SaveOf(0, 2))

# ----- 4. the named histories: §7's goldens ------------------------------------------
#
# Each history renders the golden's log as tests/test_migration_v0_3_0_to_v0_4_0.py
# builds it, up to t, created_at, ids, values' payloads and user records' bodies.
# Its truth is §2's for the story the golden's comment tells; where the golden
# states another output, the task report says so.

_N = own_process  # the claims' processes, by claim
_N1_11, _N1_12 = Process("n1", 11), Process("n1", 12)
LOSS, STATUS, STAGE = "loss", "status", "stage"
_ERR = Outcome.ERRORED


def _launched(
    claim: int, process: Process, gate: Gate, origin: Origin, launch: str
) -> Claim:
    return Claim(
        claim,
        process,
        gate,
        origin,
        launch,
        Clock.LOADED,
        Fold.AT_CLAIM,
        CutSave.LANDED,
    )


GOLDENS: dict[str, tuple[list[Row], History]] = {
    # --- lineage
    "DISPLACED": (
        goldens.DISPLACED,
        _h(
            _claim(0, Fresh()),
            _first(0, 0, LOSS),
            _first(0, 1),
            _takeover(1, FromSave(SaveOf(0, 1))),
            _finished(0, 2, LOSS),
            Beat(0, CONTROL),
            _after(1, 2),
            _after(1, 3, LOSS),
            _preempted(0),
            _completes(1),
        ),
    ),
    "ABOVE": (
        goldens.ABOVE,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            _after(0, 1),
            _takeover(1, FromSave(SaveOf(0, 1))),
            _after(1, 2),
            _after(0, 2),
            _open(0, 3, LOSS),
            _completes(1),
            _preempted(0),
        ),
    ),
    "DISPLACEMENT_PARENT": (
        goldens.DISPLACEMENT_PARENT,
        _h(
            _claim(0, Fresh()),
            _after(0, 0, LOSS),
            _after(0, 1, LOSS),
            _takeover(1, FromSave(SaveOf(0, 1))),
            _after(1, 2, LOSS),
            _after(0, 2, LOSS),
            _preempted(1),
            _preempted(0),
            _claim(2, FromSave(SaveOf(1, 2))),
            _after(2, 3, LOSS),
            _completes(2),
        ),
    ),
    "COMMITLESS": (
        goldens.COMMITLESS,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            _after(0, 1),
            _preempted(0),
            _claim(1, FromSave(SaveOf(0, 1))),
            Emit(1, STATUS, Stamp.STEPLESS),
            Beacon(1, CONTROL),
            Emit(1, STATUS, Stamp.STEPLESS),
            Beacon(1, CONTROL),
            _preempted(1),
            _claim(2, FromSave(SaveOf(0, 1))),
            _after(2, 2),
            _completes(2),
        ),
    ),
    "RESUMED_PAST_AN_UNRECORDED_STEP": (
        goldens.RESUMED_PAST_AN_UNRECORDED_STEP,
        _h(
            _claim(0, Fresh()),
            _after(0, 0, LOSS),
            _after(0, 1, LOSS),
            _finished(0, 2),
            Halt(0, Exited(1)),
            ThirdPartyStop(
                0, Release(_ERR, Via.HELPER), BACKSTOP, PROGRESS, Honors.NONE
            ),
            _claim(1, FromSave(SaveOf(0, 2))),
            _after(1, 3, LOSS),
            _after(1, 4, LOSS),
            _completes(1),
        ),
    ),
    "RESUMED_PAST_A_PREEMPTED_EXIT": (
        goldens.RESUMED_PAST_A_PREEMPTED_EXIT,
        _h(
            _claim(0, Fresh()),
            _after(0, 0, LOSS),
            _after(0, 1, LOSS),
            StopRequest("preempt"),
            _preempted(0),
            _claim(1, FromSave(SaveOf(0, 1))),
            _after_claimed(1, 2, LOSS),
            _completes(1),
        ),
    ),
    "LOADING_RETRY_BESIDE_A_DISPLACED_WORKER": (
        goldens.LOADING_RETRY_BESIDE_A_DISPLACED_WORKER,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            _after(0, 1),
            _takeover(1, FromSave(SaveOf(0, 1))),
            _exit(1, completed=False, error="OSError", final=FinalStep.TICKED),
            Halt(1, Exited(1)),
            _after(0, 2),
            _killed(0),
        ),
    ),
    "RESUMED_FROM_A_STEP_COMMITTED_AFTER_THE_CLAIM": (
        goldens.RESUMED_FROM_A_STEP_COMMITTED_AFTER_THE_CLAIM,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            _after(0, 1),
            _takeover(1, FromOutside(2)),  # A's save of 2 lands after the claim
            _after(0, 2),
            _killed(0),
            _after(1, 3),
            _completes(1),
        ),
    ),
    "DOUBLY_DISPLACED": (
        goldens.DOUBLY_DISPLACED,
        _h(
            _claim(0, Fresh()),
            run(0, range(3), BEATS),
            _takeover(1, FromSave(SaveOf(0, 0))),
            _open(0, 3, LOSS),
            _killed(0),
            _after(1, 1),
            _takeover(2, FromOutside(3)),
            _after(1, 2),
            _killed(1),
            _after(2, 4),
            _killed(2),
        ),
    ),
    "RESUMED_FROM_A_TICK_SKIPPED_AFTER_THE_CLAIM": (
        goldens.RESUMED_FROM_A_TICK_SKIPPED_AFTER_THE_CLAIM,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            _takeover(1, FromOutside(2)),
            _after(0, 1),
            _finished(0, 2),
            _after(0, 3),
            _killed(0),
            _after(1, 3),
            _completes(1),
        ),
    ),
    "RESUMED_FROM_A_TICK_SKIPPED_ACROSS_THE_CLAIM": (
        goldens.RESUMED_FROM_A_TICK_SKIPPED_ACROSS_THE_CLAIM,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            _after(0, 1),
            _finished(0, 2),
            _takeover(1, FromSave(SaveOf(0, 2))),
            _after(0, 3),
            _killed(0),
            _after(1, 3),
            _completes(1),
        ),
    ),
    "RESUMED_ON_AN_OLDER_CLAIMS_STEP": (
        goldens.RESUMED_ON_AN_OLDER_CLAIMS_STEP,
        _h(
            _claim(0, Fresh()),
            run(0, range(3), BEATS),
            _preempted(0),
            _claim(1, FromSave(SaveOf(0, 0))),
            _after(1, 1),
            _takeover(2, FromSave(SaveOf(0, 2))),
            _after(1, 2),
            _killed(1),
            _after(2, 3),
            _completes(2),
        ),
    ),
    # --- values
    "LOADING": (
        goldens.LOADING,
        _h(
            _claim(0, Fresh()),
            Emit(0, STATUS, Stamp.METRIC),
            _after(0, 0, LOSS),
            _after(0, 1, LOSS),
            _preempted(0),
            Claim(
                1,
                _N(1),
                Gate.FREE,
                FromSave(SaveOf(0, 1)),
                None,
                Clock.ZERO,
                Fold.AT_CLAIM,
                CutSave.LANDED,
            ),
            Emit(1, STATUS, Stamp.METRIC),
            _after(1, 2, LOSS),
            _completes(1),
        ),
    ),
    "SKIPPED": (
        goldens.SKIPPED,
        _h(
            _claim(0, Fresh()),
            _after(0, 0, LOSS),
            _after_tail(0, 1, LOSS),
            _finished(0, 2, LOSS, "acc"),
            _after_tail(0, 3, LOSS),
            _finished(0, 4),
            _after_tail(0, 5, LOSS),
            _completes(0),
        ),
    ),
    "POST_LOOP": (
        goldens.POST_LOOP,
        _h(
            _claim(0, Fresh()),
            _after(0, 0, LOSS),
            _after(0, 1, LOSS),
            Emit(0, "final_eval", Stamp.METRIC),
            Emit(0, "reason", Stamp.STEPLESS),
            _completes(0),
        ),
    ),
    "TRAILING": (
        goldens.TRAILING,
        _h(
            _claim(0, Fresh()),
            _after(0, 0, LOSS),
            _after_tail(0, 1, LOSS),
            _finished(0, 2, LOSS),
            _completes(0),
        ),
    ),
    "PREEMPTED": (
        goldens.PREEMPTED,
        _h(
            _claim(0, Fresh()), _after(0, 0, LOSS), _finished(0, 1, LOSS), _preempted(0)
        ),
    ),
    "ERRORED": (
        goldens.ERRORED,
        _h(
            _claim(0, Fresh()),
            _after(0, 0, LOSS),
            _open(0, 1, LOSS),
            _exit(0, completed=False, error="CUDA out of memory", final=YIELDED),
            Halt(0, Exited(1)),
        ),
    ),
    "RESUMED_PAST_A_LOADING_CRASH": (
        goldens.RESUMED_PAST_A_LOADING_CRASH,
        _h(
            _claim(0, Fresh()),
            _after(0, 0, LOSS),
            _after_tail(0, 1, LOSS),
            _finished(0, 2, LOSS),
            _exit(0, completed=False, error="CUDA out of memory", final=YIELDED),
            Halt(0, Exited(1)),
            Claim(
                1,
                _N(1),
                Gate.FREE,
                FromSave(SaveOf(0, 2)),
                None,
                Clock.ZERO,
                Fold.AT_CLAIM,
                CutSave.LANDED,
            ),
            Emit(1, STATUS, Stamp.METRIC),
            _exit(1, completed=False, error="OSError", final=YIELDED),
            Halt(1, Exited(1)),
            _claim(2, FromSave(SaveOf(0, 2))),
            _after(2, 3, LOSS),
            _completes(2),
        ),
    ),
    "RESUMED_BESIDE_A_REAL_BEAT": (
        goldens.RESUMED_BESIDE_A_REAL_BEAT,
        _h(
            _claim(0, Fresh()),
            _after(0, 0, LOSS),
            _after(0, 1, LOSS),
            _open(0, 2, LOSS),
            _exit(0, completed=False, error="CUDA out of memory", final=YIELDED),
            Halt(0, Exited(1)),
            _claim(1, FromSave(SaveOf(0, 1))),
            _after(1, 2, LOSS),
            _preempted(1),
            _claim(2, FromSave(SaveOf(1, 2))),
            _after(2, 3, LOSS),
            _completes(2),
        ),
    ),
    "LOADING_RETRY_RELEASED": (
        goldens.LOADING_RETRY_RELEASED,
        _h(
            _claim(0, Fresh()),
            _after(0, 0, LOSS),
            _exit(0, completed=False, error="boom", final=YIELDED),
            Halt(0, Exited(1)),
            _claim(1, FromSave(SaveOf(0, 0))),
            Emit(1, STATUS, Stamp.METRIC),
            Halt(1, Exited(1)),
            ThirdPartyStop(1, Release(_ERR, Via.HELPER), BACKSTOP, NULL, Honors.NONE),
            _claim(2, FromSave(SaveOf(0, 0))),
            _after(2, 1, LOSS),
            _completes(2),
        ),
    ),
    "LOADING_RETRY_UNENDED": (
        goldens.LOADING_RETRY_UNENDED,
        _h(
            _claim(0, Fresh()),
            _after(0, 0, LOSS),
            _exit(0, completed=False, error="boom", final=YIELDED),
            Halt(0, Exited(1)),
            _claim(1, FromSave(SaveOf(0, 0))),
            Emit(1, STATUS, Stamp.METRIC),
            _killed(1),
            UserRecord("user.note"),
            _claim(2, FromSave(SaveOf(0, 0))),
            _after(2, 1, LOSS),
            _completes(2),
        ),
    ),
    "LOADING_RETRY_AT_ITS_RESUME_STEP": (
        goldens.LOADING_RETRY_AT_ITS_RESUME_STEP,
        _h(
            _claim(0, Fresh()),
            _after(0, 0, LOSS),
            _after_tail(0, 1, LOSS),
            _finished(0, 2, LOSS),
            _exit(0, completed=False, error="CUDA out of memory", final=YIELDED),
            Halt(0, Exited(1)),
            _claim(1, FromSave(SaveOf(0, 2))),
            Emit(1, STATUS, Stamp.METRIC),
            Halt(1, Exited(1)),
            ThirdPartyStop(1, Release(_ERR, Via.HELPER), BACKSTOP, NULL, Honors.NONE),
            _claim(2, FromSave(SaveOf(0, 2))),
            _after(2, 3, LOSS),
            _completes(2),
        ),
    ),
    "BEATLESS_FULL_STEP": (
        goldens.BEATLESS_FULL_STEP,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            _after(0, 1),
            _preempted(0),
            _claim(1, FromSave(SaveOf(0, 1))),
            _finished(1, 2, LOSS, STATUS),
            Halt(1, Exited(1)),
            ThirdPartyStop(
                1, Release(_ERR, Via.HELPER), BACKSTOP, PROGRESS, Honors.NONE
            ),
            _claim(2, FromSave(SaveOf(1, 2))),
            _after(2, 3, LOSS),
            _completes(2),
        ),
    ),
    "AFTER_STOPPED": (
        goldens.AFTER_STOPPED,
        _h(
            _claim(0, Fresh()),
            _after(0, 0, LOSS),
            _exit(0, completed=True, error=None, final=YIELDED),
            Emit(0, STATUS, Stamp.METRIC),
            Emit(0, "reason", Stamp.STEPLESS),
            Halt(0, Exited(0)),
        ),
    ),
    "AFTER_AN_ATTRIBUTED_DEATH": (
        goldens.AFTER_AN_ATTRIBUTED_DEATH,
        _h(
            Launched("L1", _N(0)),
            _launched(0, _N(0), Gate.FREE, Fresh(), "L1"),
            _after(0, 0),
            _finished(0, 1, LOSS),
            _killed(0),
            Reaped("L1", Killed(9)),
            Stray(LOSS, 1),  # a process that holds no claim yet
            _claim(1, FromSave(SaveOf(0, 1))),
            _after(1, 2),
            _killed(1),
        ),
    ),
    # --- records
    "EXIT": (
        goldens.EXIT,
        _h(_claim(0, Fresh()), _after(0, 0), StopRequest("halt"), _preempted(0)),
    ),
    "LAUNCHED": (
        goldens.LAUNCHED,
        _h(
            Launched("L1", _N(0)),
            _launched(0, _N(0), Gate.FREE, Fresh(), "L1"),
            _after(0, 0),
            _completes(0),
            Reaped("L1", Exited(0)),
        ),
    ),
    "WATERMARKED": (
        goldens.WATERMARKED,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            _finished(0, 1),
            _after(0, 2),
            StopRequest("halt"),
            Beacon(0, CONTROL),
            _after(0, 3),
            _preempted(0),
        ),
    ),
    "COOPERATIVE": (
        goldens.COOPERATIVE,
        _h(
            _claim(0, Fresh()),
            _after(0, 0, LOSS),
            StopRequest("timeout"),
            _after(0, 1, LOSS),
            Emit(0, "completion_reason", Stamp.STEPLESS),
            _exit(0, completed=False, error=None, final=YIELDED),
            Emit(0, STATUS, Stamp.STEPLESS),
            Halt(0, Exited(0)),
        ),
    ),
    # --- third parties
    "BACKSTOPPED": (
        goldens.BACKSTOPPED,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            Halt(0, Exited(1)),
            ThirdPartyStop(
                0, Release(_ERR, Via.HELPER), BACKSTOP, PROGRESS, Honors.NONE
            ),
            StopRequest("staged"),
            ThirdPartyStop(0, Report(None), None, PROGRESS, Honors.UNDISCHARGED),
        ),
    ),
    "LATE_BACKSTOP": (
        goldens.LATE_BACKSTOP,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            _exit(0, completed=False, error=None, final=YIELDED),
            Halt(0, Exited(1)),
            ThirdPartyStop(0, Report(Exited(1)), BACKSTOP, PROGRESS, Honors.NONE),
        ),
    ),
    "LATER_ERROR": (
        goldens.LATER_ERROR,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            _preempted(0),
            ThirdPartyStop(
                0, Release(_ERR, Via.HELPER), "slurm: NODE_FAIL", NULL, Honors.NONE
            ),
        ),
    ),
    "LATER_DEFAULT": (
        goldens.LATER_DEFAULT,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            StopRequest("halt"),
            ThirdPartyStop(0, Report(None), None, PROGRESS, Honors.NONE),
            _completes(0),
        ),
    ),
    "LATER_ERROR_HONORED": (
        goldens.LATER_ERROR_HONORED,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            StopRequest("halt"),
            Exit(0, False, None, YIELDED, Drain.TICKS, discards=False),
            Halt(0, Exited(0)),
            ThirdPartyStop(
                0,
                Release(_ERR, Via.HELPER),
                "slurm: NODE_FAIL",
                NULL,
                Honors.UNDISCHARGED,
            ),
        ),
    ),
    "CLAIMLESS": (
        goldens.CLAIMLESS,
        _h(
            StopRequest("pre"),
            ThirdPartyStop(None, Report(None), None, NULL, Honors.UNDISCHARGED),
        ),
    ),
    "CLAIMLESS_ERROR": (
        goldens.CLAIMLESS_ERROR,
        _h(
            StopRequest("pre"),
            ThirdPartyStop(None, Report(None), "boom", NULL, Honors.UNDISCHARGED),
        ),
    ),
    "CLAIMLESS_BACKSTOP": (
        goldens.CLAIMLESS_BACKSTOP,
        _h(
            StopRequest("pre"),
            ThirdPartyStop(
                None,
                Report(Killed(9)),
                "subprocess exited -9 with no terminal record",
                NULL,
                Honors.UNDISCHARGED,
            ),
            _claim(0, Fresh()),
            _killed(0),
        ),
    ),
    "SECOND_BACKSTOP": (
        goldens.SECOND_BACKSTOP,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            Halt(0, Exited(1)),
            ThirdPartyStop(
                0, Release(_ERR, Via.HELPER), BACKSTOP, PROGRESS, Honors.NONE
            ),
            ThirdPartyStop(
                0,
                Report(Exited(137)),
                "subprocess exited 137 with no terminal record",
                PROGRESS,
                Honors.NONE,
            ),
        ),
    ),
    "BACKSTOP_ID_TAKEN": (
        goldens.BACKSTOP_ID_TAKEN,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            StopRequest("backstop@5"),
            _exit(0, completed=False, error=None, final=YIELDED),
            Halt(0, Exited(1)),
            ThirdPartyStop(0, Report(Exited(1)), BACKSTOP, PROGRESS, Honors.NONE),
        ),
    ),
    # --- lone stops
    "BEATEN_AFTER": (
        goldens.BEATEN_AFTER,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            ThirdPartyStop(0, Report(None), None, PROGRESS, Honors.NONE),
            _after(0, 1),
            _killed(0),
        ),
    ),
    "RECLAIM_REPORTED_LATER": (
        goldens.RECLAIM_REPORTED_LATER,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            _after(0, 1),
            _open(0, 2, LOSS),
            _killed(0),
            ThirdPartyStop(
                0, Release(Outcome.PREEMPTED, Via.HELPER), None, NULL, Honors.NONE
            ),
        ),
    ),
    "RECLAIM_OF_10_23": (
        goldens.RECLAIM_OF_10_23,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            _after(0, 1),
            _open(0, 2, LOSS),
            _killed(0),
            ThirdPartyStop(
                0, Release(_ERR, Via.HELPER), goldens.SACCT, NULL, Honors.NONE
            ),
        ),
    ),
    "REPORTED_LATER_BOUNDARY": (
        goldens.REPORTED_LATER_BOUNDARY,
        _h(
            _claim(0, Fresh()),
            _after(0, 0),
            _preempted(0),
            _claim(1, FromSave(SaveOf(0, 0))),
            _after(1, 1),
            _killed(1),
            ThirdPartyStop(
                1, Release(Outcome.LOST, Via.HELPER), None, PROGRESS, Honors.NONE
            ),
        ),
    ),
    # --- attributed deaths
    "ATTRIBUTED": (
        goldens.ATTRIBUTED,
        _h(
            Launched("L1", _N(0)),
            _launched(0, _N(0), Gate.FREE, Fresh(), "L1"),
            _after(0, 0),
            _killed(0),
            Reaped("L1", Killed(9)),
        ),
    ),
    "WRAPPER": (
        goldens.WRAPPER,
        _h(
            Launched("L1", Process("n1", 100)),
            _launched(0, Process("n1", 101), Gate.FREE, Fresh(), "L1"),
            _after(0, 0),
            _killed(0),
            Reaped("L1", Exited(0)),
        ),
    ),
    "ANOTHER_HOST": (
        goldens.ANOTHER_HOST,
        _h(
            Launched("L1", Process("n2", 11)),
            _launched(0, Process("n1", 11), Gate.FREE, Fresh(), "L1"),
            _after(0, 0),
            _killed(0),
            Reaped("L1", Exited(1)),
        ),
    ),
    "LATE_REAP": (
        goldens.LATE_REAP,
        _h(
            _launched(0, _N1_11, Gate.FREE, Fresh(), "L1"),
            Launched("L1", _N1_11),
            _after(0, 0),
            Halt(0, Exited(1)),
            Launched("L2", _N1_12),
            _launched(1, _N1_12, Gate.FREE, FromSave(SaveOf(0, 0)), "L2"),
            _after(1, 1),
            _completes(1),
            Reaped("L1", Exited(1)),
            Reaped("L2", Exited(0)),
        ),
    ),
    "TWO_CLAIMS_ONE_LAUNCH": (
        goldens.TWO_CLAIMS_ONE_LAUNCH,
        _h(
            Launched("L1", _N1_11),
            _launched(0, _N1_11, Gate.FREE, Fresh(), "L1"),
            _after(0, 0),
            Halt(0, None),  # its Worker abandoned; the process runs a second one
            _launched(1, _N1_11, Gate.TAKEOVER, FromSave(SaveOf(0, 0)), "L1"),
            _after(1, 1),
            Halt(1, Exited(0)),
            Reaped("L1", Exited(0)),
        ),
    ),
}
"""Every golden §7 lists, as a history, but the three a conforming writer cannot
write: RECLAIMED, LATER_RELEASE and CLAIMLESS_RELEASE each hold the reclaim
tool's malformed ``stopped`` body. The audit's goldens and the third place are
named above with their contents, as §7 states them."""


# A stop drained at a tick and withdrawn before the next commit (the fix round 2
# re-review's probe): iteration 0 closes; stop x0 arrives; the worker ticks,
# draining x0, as its watermark records; a third party clears x0; iteration 1
# closes; the worker exits, honouring x0. Its native drains follow the 0.3.0
# watermarks (§7's target), so its tick takes x0 in before the withdrawal, and
# its native exit honours x0 as its 0.3.0 one does (E2: the withdrawal stays at
# its answer). Round 2's "drained at the commit", which differed only in a native
# cadence, went with the cadence.
DRAINED_AT_THE_TICK = _h(
    _claim(0, Fresh()),
    Begin(0, 0),
    Finish(0, True, TICKED),
    StopRequest("x0"),
    Beat(0, CONTROL),
    ThirdPartyStop(0, Report(None), None, NULL, Honors.UNDISCHARGED),
    _after(0, 1),
    _preempted(0),
)


# CLEARED_AFTER_THE_END (§7, E2; task-17-convention-table.md): in a tick-first
# loop the worker ticks 0; stop x0 arrives, and the pass of 0 closes; a third
# party's note clears x0; the worker ticks 1 without taking x0 in, an at-drain
# writer, and exits honouring nothing. The native commit of 0 drains x0 before
# its answer, where the withdrawal stands (§7, E2), so the native exit honours
# x0: E2's second stated cost. Taken in at the claim instead, the 0.3.0 exit
# honours x0, and so does the native one.
def _cleared(fold: Fold) -> History:
    return _h(
        Claim(
            0,
            own_process(0),
            Gate.FREE,
            Fresh(),
            None,
            Clock.LOADED,
            fold,
            CutSave.LANDED,
        ),
        Begin(0, 0),
        Beat(0, CONTROL),
        StopRequest("x0"),
        Emit(0, VALUE, Stamp.METRIC),
        Finish(0, True, Undated.OWN_LAST),
        ThirdPartyStop(0, Report(None), None, NULL, Honors.UNDISCHARGED),
        _first(0, 1, VALUE),
        _preempted(0),
    )


CLEARED_AFTER_THE_END = _cleared(Fold.AT_DRAIN)
CLEARED_TAKEN_IN = _cleared(Fold.AT_CLAIM)

# A third party's release written directly, not through report_death: with a
# cause, a death report even on a claim whose exit is readable (§3: "any party
# ... may write one"; a cause reports "whether or not the claim has ended");
# without one, none, since it "reports only while the claim is held".
TPS_RELEASE_AFTER_EXIT_DIRECT = _after_exit(Release(Outcome.LOST, Via.DIRECT), None)
TPS_CAUSE_AFTER_EXIT_DIRECT = _after_exit(
    Release(Outcome.ERRORED, Via.DIRECT), "slurm: NODE_FAIL"
)


# The release and takeover orders of a fenced worker's own stopped (the review's
# fenced_two_orders, histories A and B; task-17-convention-table.md, Residues).
# A: the released worker's stopped honours x0 before its successor's started;
# the successor's claim-time fold holds x0 spent, and its exit honours nothing,
# so its native exit honours nothing too. B: the displaced worker's stopped
# honours x0 after its successor's claim; the successor beat past x0 and its
# 0.3.0 exit honours it, but the withdrawal at the answer precedes its native
# drains, so its native exit omits x0 (§7, E2's third cost).
RELEASE_ORDER = _h(
    _claim(0, Fresh()),
    run(0, [0], SAVES),
    StopRequest("x0"),
    _released(0, Outcome.LOST),
    _preempted(0),
    _claim(1, FromSave(SaveOf(0, 0))),
    run(1, [1], SAVES),
    _preempted(1),
)
TAKEOVER_ORDER = _h(
    _claim(0, Fresh()),
    run(0, [0], SAVES),
    StopRequest("x0"),
    _takeover(1, FromSave(SaveOf(0, 0))),
    _preempted(0),
    run(1, [1], SAVES),
    _preempted(1),
)

# The Task 18 re-review's histories A, B and J, where the withdrawn E3 moved a
# stop across another worker's read; under E2 no stop moves. A: C0's tick reads x0 (consumed_seq 2) and C0 is
# killed; C1 claims and exits, listing only its ticks' reads, none. B: C0,
# displaced by a takeover and still live, beats past x0 after C1's claim; C1's
# exit lists only its ticks' reads, none. J: as A, then C1 commits a pass with
# no tick before its exit; C2 claims, a third party's note answers x0, and C2,
# folding at its claim, ticks past x0 and honours it.
REREVIEW_A = _h(
    _claim(0, Fresh()),
    StopRequest("x0"),
    run(0, [0], SAVES),
    _killed(0),
    _claim(1, FromSave(SaveOf(0, 0))),
    Exit(1, False, "OSError", YIELDED, Drain.TICKS, discards=False),
    Halt(1, Exited(1)),
)
REREVIEW_B = _h(
    _claim(0, Fresh()),
    run(0, [0], SAVES),
    _takeover(1, FromSave(SaveOf(0, 0))),
    StopRequest("x0"),
    run(0, [1], SAVES),
    _killed(0),
    Exit(1, False, None, YIELDED, Drain.TICKS, discards=False),
    Halt(1, Exited(0)),
)
REREVIEW_J = _h(
    _claim(0, Fresh()),
    StopRequest("x0"),
    run(0, [0], SAVES),
    _killed(0),
    _claim(1, FromSave(SaveOf(0, 0))),
    Begin(1, 1),
    Emit(1, VALUE, Stamp.METRIC),
    Finish(1, True, Undated.OWN_LAST),
    Exit(1, False, None, YIELDED, Drain.TICKS, discards=False),
    Halt(1, Exited(0)),
    _claim(2, FromSave(SaveOf(1, 1))),
    ThirdPartyStop(2, Report(None), None, NULL, Honors.UNDISCHARGED),
    run(2, [2], SAVES),
    _preempted(2),
)


# MIDSAVE (task-17-convention-table.md, cut-save): mycooc's pass at 0 and 1; at
# 2 it saves, ticks, writes its metric, and is killed before its stage; the
# next claim loads the save of 2 and completes 3. Whether 2 finished is the
# writer fact cut_save.
def _midsave(cut_save: CutSave, landed: bool) -> History:
    first = Claim(
        0,
        own_process(0),
        Gate.FREE,
        Fresh(),
        None,
        Clock.LOADED,
        Fold.AT_CLAIM,
        cut_save,
    )
    loaded = SaveOf(0, 2) if landed else SaveOf(0, 1)
    return _h(
        first,
        run(0, [0, 1], MYCOOC),
        iteration(0, Step(2, MYCOOC), cut=Cut.AFTER_SAVE if landed else Cut.MID_SAVE),
        _killed(0),
        _claim(1, FromSave(loaded)),
        run(1, [3], MYCOOC),
        _completes(1),
    )


# MIDSAVE_EXIT: as MIDSAVE, but at 2 the worker's own preempted stopped cuts the
# pass after its save, the exit keeping the open iteration or discarding it.
def _midsave_exit(cut_save: CutSave, discards: bool) -> History:
    first = Claim(
        0,
        own_process(0),
        Gate.FREE,
        Fresh(),
        None,
        Clock.LOADED,
        Fold.AT_CLAIM,
        cut_save,
    )
    return _h(
        first,
        run(0, [0, 1], MYCOOC),
        iteration(0, Step(2, MYCOOC), cut=Cut.AFTER_SAVE),
        Exit(0, False, None, YIELDED, Drain.EXIT, discards),
        Halt(0, Exited(0)),
        _claim(1, FromSave(SaveOf(0, 2))),
        run(1, [3], MYCOOC),
        _completes(1),
    )


MIDSAVE_LANDED = _midsave(CutSave.LANDED, landed=True)
MIDSAVE_CLOSED = _midsave(CutSave.CLOSED, landed=True)
MIDSAVE_SAVE_START = _midsave(CutSave.SAVE_START, landed=True)
UNLANDED_SAVE_START = _midsave(CutSave.SAVE_START, landed=False)
UNLANDED_LANDED = _midsave(CutSave.LANDED, landed=False)

# The end-time fork (§7): GAP_ITERATION's unlogged iteration 1 ended with its
# claim's newest record, or just before the log's next record. The log is one.
GAP_ITERATION_LATE = _h(
    _claim(0, Fresh()),
    run(0, [0], BEATS),
    run(0, [1], replace(UNLOGGED, ended=Undated.NEXT)),
    run(0, [2], BEATS),
    _completes(0),
)

# d78b72f1 in mycooc's real record order, as the store copy holds it: each pass
# writes its status, saves (unlogged), ticks (except at 130 and at each claim's
# last pass), writes its metrics, percentile and stage, then closes. At 181 and
# 182 each claim then wrote status ``saving results`` and died: claim 1 killed
# with its job and released by the reclaim tool, claim 2 by torch.save's
# failure. The truth is the same under every cut_save value: every pass closed.
_METRICS = (
    "mean_p1",
    "mean_mrr",
    "mean_cos_p1",
    "mean_mnn_rate",
    "mean_geom_rank",
    "mean_norm_info",
    "mean_nhmr",
    "mean_gep_p1",
    "mean_gep_norm_info",
    "mean_cycle_acc",
    "mean_soft_cycle_mrr",
    "mean_soft_cycle_trace",
    "mean_unique_coverage",
    "mean_equality",
    "mean_entropy",
    "mean_mi",
    "mean_coverage_entropy",
)
_LATER_CLAIMS = (*_METRICS, "mean_relational_fidelity")
"""The metrics of claim 0's step 0, and of every step of the later claims."""
_CHURNED = (*_METRICS, "mean_argmax_churn", "mean_relational_fidelity")
"""The metrics of claim 0's steps 1 and 131: no ``percentile``."""
_FIRST_CLAIMS = (*_CHURNED, "percentile")
"""The metrics of claim 0's other steps, 2 to 181."""


def _mycooc_pass(
    claim: int, step: int, metrics: tuple[str, ...], *, ticks: bool
) -> list[Event]:
    events: list[Event] = [Begin(claim, step)]
    if step:
        events.append(Emit(claim, STATUS, Stamp.REGISTER))
    events.append(PassSave(claim, landed=True, cut_end=Undated.OWN_LAST))
    if ticks:
        events.append(Beat(claim, CONTROL))
    events += _emits(claim, (*metrics, STAGE))
    return events + [Finish(claim, False, Undated.OWN_LAST)]


def _d78_real(cut_save: CutSave) -> History:
    def claim(c: int, pid: int, origin: Origin, launch: str, clock: Clock) -> Claim:
        return Claim(
            c,
            Process("ai04", pid),
            Gate.FREE,
            origin,
            launch,
            clock,
            Fold.AT_CLAIM,
            cut_save,
        )

    loading = [Emit(0, STATUS, Stamp.REGISTER)] * 4
    first: list[Event] = [
        claim(0, 3944963, Fresh(), "53375:0", Clock.LOADED),
        Emit(0, "config", Stamp.STEPLESS),
        *loading,
        *_mycooc_pass(0, 0, _LATER_CLAIMS, ticks=True),
    ]
    for s in range(1, 182):
        metrics = _CHURNED if s in (1, 131) else _FIRST_CLAIMS
        first += _mycooc_pass(0, s, metrics, ticks=s not in (130, 181))

    def later(c: int, pid: int, launch: str, step: int) -> list[Event]:
        return [
            claim(c, pid, FromSave(SaveOf(c - 1, step - 1)), launch, Clock.ZERO),
            *[Emit(c, STATUS, Stamp.REGISTER)] * 4,
            *_mycooc_pass(c, step, _LATER_CLAIMS, ticks=False),
            Emit(c, STATUS, Stamp.REGISTER),  # saving results
        ]

    born_0_2_0 = _h(
        first,
        Emit(0, STATUS, Stamp.REGISTER),  # saving results
        _killed(0),
        ThirdPartyStop(
            0, Release(Outcome.PREEMPTED, Via.HELPER), None, NULL, Honors.NONE
        ),
        later(1, 3954611, "53421:0", 182),
        _exit(
            1,
            completed=False,
            error="[enforce fail at inline_container.cc:603] . unexpected pos 576 vs 470",
            final=FinalStep.TICKED,
        ),
        Halt(1, Exited(1)),
        later(2, 3956995, "53451:0", 183),
        Emit(2, "completion_reason", Stamp.STEPLESS),
        _exit(2, completed=True, error=None, final=FinalStep.FINISHED),
        Emit(2, STATUS, Stamp.REGISTER),  # done
        Halt(2, Exited(0)),
    )
    # the real log was born in 0.2.0: its honored and claim_seq are the 0.2.0 ->
    # 0.3.0 step's positional names (task-19-rereview.md, Minor B)
    return replace(born_0_2_0, born=Born.V0_2_0)


D78_REAL = _d78_real(CutSave.LANDED)

_Record = tuple[str, str | None, int | None]


def _d78_store_copy() -> list[_Record]:
    """d78b72f1's log as the store copy holds it, all 4,243 records, as
    (topic, name, step), transcribed from it record by record:
    - claim 0's ``started``, its ``config`` register and four loading
      ``status`` values at 0;
    - its passes 0 to 181: a ``status`` (none at 0), a heartbeat (none at 130
      or 181), the metrics and ``stage``;
    - its ``status`` "saving results" at 181, and the reclaim tool's ``stopped``;
    - claims 1 and 2, at 182 and 183: ``started``, four loading ``status``
      values at 0, one beatless pass, and a ``status``; claim 1's error exit;
      claim 2's ``completion_reason``, completed exit and final ``status``.

    ``D78_DIGEST`` pins the transcription to the store copy."""

    def metrics(step: int, names: tuple[str, ...]) -> list[_Record]:
        return [("value", m, step) for m in (*names, "stage")]

    loading: list[_Record] = [("value", "status", 0)] * 4
    log: list[_Record] = [
        ("lifecycle.started", None, None),
        ("value", "config", None),
        *loading,
        ("lifecycle.heartbeat", None, 0),
        *metrics(0, _LATER_CLAIMS),
    ]
    for s in range(1, 182):
        log.append(("value", "status", s))
        if s not in (130, 181):
            log.append(("lifecycle.heartbeat", None, s))
        log += metrics(s, _CHURNED if s in (1, 131) else _FIRST_CLAIMS)
    log += [("value", "status", 181), ("lifecycle.stopped", None, None)]
    for s in (182, 183):
        log += [("lifecycle.started", None, None), *loading, ("value", "status", s)]
        log += [*metrics(s, _LATER_CLAIMS), ("value", "status", s)]
        if s == 182:
            log.append(("lifecycle.stopped", None, None))
    log += [
        ("value", "completion_reason", None),
        ("lifecycle.stopped", None, None),
        ("value", "status", 183),
    ]
    return log


D78_DIGEST = "821ec37b0f6667ec6347f0a1ecd143c043abb53ca3f78696120a1ca288b13cb6"
"""The sha256 of the store copy's (topic, name, step) sequence, as JSON: its
``log`` table read ``mode=ro&immutable=1``, in seq order, a step read from a
``value`` or a heartbeat. 4,243 records."""

# A synthetic writer whose version changes mid-root (Review Focus 9), on
# mycooc's pass shape: its old version writes its stopped, then a save phase
# (``status: saving``, the save); its new one the save phase, then its stopped.
# Claim 0 ran the old version, and its save names its extendable exit; claim 1
# the new, and its save names its last commit (§6, "Save"). No mycooc version
# is the old one: every W1 revision force-saves its resume checkpoint before its
# stop (task-19-report.md, P-a), so this is §6's rule, D3, on a writer of ours.
SYNTHETIC_VERSION_CHANGE = _h(
    _claim(0, Fresh()),
    run(0, [0, 1, 2], MYCOOC),
    _exit(0, completed=False, error=None, final=FinalStep.FINISHED),
    Emit(0, STATUS, Stamp.REGISTER),  # saving
    Save(0),
    Emit(0, STATUS, Stamp.REGISTER),  # done
    Halt(0, Exited(0)),
    _claim(1, FromSave(ExitSave(0))),
    run(1, [3, 4, 5], MYCOOC),
    Emit(1, STATUS, Stamp.REGISTER),  # saving
    Save(1),
    _exit(1, completed=False, error=None, final=FinalStep.FINISHED),
    Emit(1, STATUS, Stamp.REGISTER),  # done
    Halt(1, Exited(0)),
    _claim(2, FromSave(SaveOf(1, 5))),
    run(2, [6], MYCOOC),
    _completes(2),
)

# SLOT (the critique's P1): C1 beats 0-3, having saved only 0 and 1, and is
# killed; C2 resumes from 1, beats 2 and runs on unlogged to 3, saving it, and
# is killed; C3 resumes from C2's 3, beats 4 and 5, and completes. Under
# load=newest it is the one history with SLOT's log: C3 <- C2@3.
SLOT = _h(
    _claim(0, Fresh()),
    run(0, [0, 1], SAVES),
    run(0, [2, 3], UNSAVED),
    _killed(0),
    _claim(1, FromSave(SaveOf(0, 1))),
    run(1, [2], UNSAVED),
    run(1, [3], UNLOGGED),
    _killed(1),
    _claim(2, FromSave(SaveOf(1, 3))),
    run(2, [4, 5], UNSAVED),
    _completes(2),
)
SLOT_OLDER = _h(
    _claim(0, Fresh()),
    run(0, [0, 1], SAVES),
    run(0, [2], UNSAVED),
    run(0, [3], SAVES),
    _killed(0),
    _claim(1, FromSave(SaveOf(0, 1))),
    run(1, [2], UNSAVED),
    run(1, [3], UNLOGGED),
    _killed(1),
    _claim(2, FromSave(SaveOf(0, 3))),
    run(2, [4, 5], UNSAVED),
    _completes(2),
)


NAMED = {
    "stop-author H1": STOP_AUTHOR_EXIT,
    "stop-author H2": STOP_AUTHOR_THIRD_PARTY,
    "third-party-stop H1": TPS_CLEARS_STAGED,
    "third-party-stop H1, a note after the exit": TPS_NOTE_AFTER_EXIT,
    "third-party-stop H2, a loser's reap after the exit": TPS_LOSER_REAPED_AFTER_EXIT,
    "third-party-stop, a release after the exit": TPS_RELEASE_AFTER_EXIT,
    "third-party-stop, a cause after the exit": TPS_CAUSE_AFTER_EXIT,
    "third-party-stop, a note with an error after the exit": TPS_NOTE_WITH_ERROR_AFTER_EXIT,
    "third-party-stop H2, a release of a dead claim": TPS_RELEASE_OF_DEAD,
    "third-party-stop, a note on a dead claim": TPS_NOTE_ON_DEAD,
    "value-claim H1": VALUE_CLAIM_SUCCESSORS,
    "value-claim H2": VALUE_CLAIM_DISPLACED,
    "value-claim H1, after an exit": VALUE_CLAIM_EXITED_SUCCESSORS,
    "value-claim H2, a teardown value": VALUE_CLAIM_TEARDOWN,
    "value-claim, a dead claim no stopped named": VALUE_CLAIM_DEAD_UNNAMED,
    "value-claim, a takeover": VALUE_CLAIM_TAKEN_OVER,
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
    "final-step H2, discarded": FINAL_OPEN_DISCARDED,
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
    "SUCCESSOR_PROVED_DEAD, taken over": SUCCESSOR_TOOK_OVER,
    "THIRD_PLACE_MOVES_PAST": THIRD_PLACE_MOVES_PAST,
    "displaced, saved before the release": DISPLACED_SAVED_FIRST,
    "displaced, finished after the release": DISPLACED_SAVED_AFTER,
    "a synthetic writer's post-exit save": SYNTHETIC_POST_EXIT_SAVE,
    "a synthetic writer's post-exit save, the commit's": SYNTHETIC_POST_EXIT_COMMIT,
    "drained at the tick": DRAINED_AT_THE_TICK,
    "cleared after the end": CLEARED_AFTER_THE_END,
    "release order": RELEASE_ORDER,
    "takeover order": TAKEOVER_ORDER,
    "re-review A": REREVIEW_A,
    "re-review B": REREVIEW_B,
    "re-review J": REREVIEW_J,
    "MIDSAVE, landed": MIDSAVE_LANDED,
    "MIDSAVE, closed": MIDSAVE_CLOSED,
    "MIDSAVE, save-start": MIDSAVE_SAVE_START,
    "a save that never landed, save-start": UNLANDED_SAVE_START,
    "a save that never landed, landed": UNLANDED_LANDED,
    "valueless-gap H1, ended late": GAP_ITERATION_LATE,
    "d78b72f1 in mycooc's order": D78_REAL,
    "a synthetic writer whose version changes mid-root": SYNTHETIC_VERSION_CHANGE,
    "SLOT": SLOT,
    "SLOT, the older save": SLOT_OLDER,
    "cleared after the end, taken in at the claim": CLEARED_TAKEN_IN,
    "third-party-stop, a direct release after the exit": TPS_RELEASE_AFTER_EXIT_DIRECT,
    "third-party-stop, a direct cause after the exit": TPS_CAUSE_AFTER_EXIT_DIRECT,
    **{f"golden {name}": history for name, (_, history) in GOLDENS.items()},
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
    Shared("value-claim, a dead claim no stopped named", "value-claim, a takeover"),
    Shared("beat-ends-iteration H1", "beat-ends-iteration H2"),
    Shared("post-beat-values H1", "post-beat-values H2"),
    Shared("leading-values H1", "leading-values H2"),
    Shared("beatless-claim H1", "beatless-claim H2"),
    Shared("d78b72f1", "d78b72f1, 181 unfinished"),
    Shared("valueless-gap H1", "valueless-gap H2"),
    Shared("final-step H1", "final-step H2"),
    Shared("final-step H2", "final-step H2, discarded"),
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
    Shared(
        "a synthetic writer's post-exit save",
        "a synthetic writer's post-exit save, the commit's",
    ),
    Shared("MIDSAVE, landed", "MIDSAVE, closed"),
    Shared("a save that never landed, save-start", "a save that never landed, landed"),
    Shared("valueless-gap H1", "valueless-gap H1, ended late"),
    Shared("SLOT", "SLOT, the older save"),
    Shared(
        "third-party-stop, a note with an error after the exit",
        "third-party-stop, a direct cause after the exit",
    ),
]
"""Same log, different truths: each pair needs a site."""

SETTLED = [
    Shared("SUCCESSOR_PROVED_DEAD", "SUCCESSOR_PROVED_DEAD, taken over"),
    Shared("MIDSAVE, landed", "MIDSAVE, save-start"),
    Shared(
        "third-party-stop H1, a note after the exit",
        "third-party-stop, a release after the exit",
    ),
    Shared(
        "third-party-stop H1, a note after the exit",
        "third-party-stop, a direct release after the exit",
    ),
    Shared(
        "third-party-stop, a note with an error after the exit",
        "third-party-stop, a cause after the exit",
    ),
]
"""Same log, same truth: a takeover writes the claim path's death report, as a
dead claim's successor does, and a worker that writes nothing after it leaves
nothing to fence; and on a claim whose exit is readable a release says what a
note says, unless it has a cause and is written directly (``AMBIGUOUS``): the
helper ``report_death`` writes nothing there, and evidence without a cause
(``lost``) reports only while the claim is held, whoever writes it (§3)."""


@pytest.mark.parametrize("name", list(NAMED))
def test_every_named_history_is_generable(
    name: str, conforms_to_0_3_0: Callable[[Rendered], None]
) -> None:
    h = NAMED[name]
    walk(h)  # raises NotConforming for a history no conforming writer produces
    conforms_to_0_3_0(render(h))
    assert violations(truth(h)) == ()


@pytest.mark.parametrize("name", list(GOLDENS))
def test_each_golden_history_renders_the_goldens_log(name: str) -> None:
    rows, history = GOLDENS[name]
    golden = [
        {
            "topic": r.topic,
            "name": r.name,
            "request_id": r.request_id,
            "body": json.loads(r.body),
        }
        for r in rows
    ]
    assert _normalised(records(render(history)), payloads=False) == _normalised(
        golden, payloads=False
    )


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
    its name and step (``v@4``; ``loss@4``)."""
    t = truth(h)
    rendered = render(h)
    labels = steps(h)
    claims = {
        seq: ev.claim
        for ev, seq in zip(h.events, rendered.seqs)
        if isinstance(ev, Claim) and seq is not None
    }
    payloads = {
        r.seq: f"{r.name}@{json.loads(r.body)['step']}"
        for r in rendered.rows
        if r.topic == "value"
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


def test_the_structural_check_catches_each_rule() -> None:
    """``violations`` is not vacuous: a truth broken each way is caught."""
    t = truth(_h(_claim(0, Fresh()), run(0, range(2), SAVES), _killed(0)))
    first, second = t.lineage.commits
    assert (first.values, second.values) == ((2,), (4,))  # the values' seqs
    for bad, rule in [
        (second._replace(parent=CommitId(1, 7)), "not an extendable node"),
        (first._replace(parent=second.node), "does not precede it"),
        (second._replace(values=(2, 4)), "named twice"),
        (first._replace(values=(4,)), "follows it"),
        (first._replace(t=1.0), "precedes seq 2"),
    ]:
        assert any(rule in v for v in violations(_with(t, bad))), rule
    exited = truth(STOP_AUTHOR_EXIT)
    twice = exited._replace(places=exited.places + exited.places[-1:])
    assert any("2 exits" in v for v in violations(twice))


# ----- the entries' truths -------------------------------------------------------------


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


def test_a_cause_after_the_exit_reports_no_death() -> None:
    """D4: a cause-bearing release after the worker's own exit is no death
    report, since ``report_death`` "returns None when the claim has a readable
    exit" (§3). Golden LATER_ERROR states a death."""
    assert describe(TPS_CAUSE_AFTER_EXIT)[-1] == "c0:exit extendable <- c0@1 []"
    assert not any("died" in line for line in describe(NAMED["golden LATER_ERROR"]))


def test_a_direct_report_after_the_exit_is_a_death_only_with_a_cause() -> None:
    """``Via.DIRECT``: a party that writes ``supervisor.died`` itself reports a
    cause "whether or not the claim has ended", but evidence without a cause
    "reports only while the claim is held" (§3), so a direct ``lost`` after the
    exit is no death report."""
    assert not any(" died " in line for line in describe(TPS_RELEASE_AFTER_EXIT_DIRECT))
    assert describe(TPS_CAUSE_AFTER_EXIT_DIRECT)[-2:] == [
        "c0:exit extendable <- c0@1 []",
        "c0 died errored",
    ]


def test_drains_follow_the_watermarks() -> None:
    """The native counterpart drains at each commit, each 0.3.0 tick and beacon,
    and its exit (§7's target, third item): a stop the worker's tick took in
    before a third party cleared it is honoured, and the withdrawal stands at
    its answer (E2)."""
    assert describe(DRAINED_AT_THE_TICK)[-3:] == [
        "withdraw x0",
        "c0@1 <- c0@0 []",
        "c0:exit extendable <- c0@1 [] honors x0",
    ]


def test_cleared_after_the_end_is_e2s_second_cost() -> None:
    """CLEARED_AFTER_THE_END: a tick-first loop, whose stop x0 is cleared
    between the end of iteration 0 and the next tick. The withdrawal stands at
    its answer (§7, E2), the native commit of 0 read x0 before it, so the native
    exit honours x0, which its 0.3.0 list omits: E2's case 2
    (``CLEARED_AFTER_DRAIN``)."""
    assert describe(CLEARED_AFTER_THE_END)[1:] == [
        "c0@0 <- root [v@0]",
        "withdraw x0",
        "c0@1 <- c0@0 [v@1]",
        "c0:exit extendable <- c0@1 [] honors x0",
    ]
    (cost,) = truth(CLEARED_AFTER_THE_END).costs
    assert (cost.request_id, cost.case) == ("x0", Case.CLEARED_AFTER_DRAIN)
    # taken in at the claim, the 0.3.0 exit honours x0 too: no cost
    assert describe(CLEARED_TAKEN_IN)[-1] == "c0:exit extendable <- c0@1 [] honors x0"
    assert truth(CLEARED_TAKEN_IN).costs == ()


def _cleared_after_drain(tick_first: bool, ticks_after: bool, honors: Drain) -> History:
    """One at-drain pass that reads x0 at its commit; a third party's note clears
    x0 after the close; then the pass's own tick (``ticks_after``), and an exit
    listing ``honors``."""
    opening: list[Event] = [Begin(0, 0)]
    if tick_first:
        opening.append(Beat(0, CONTROL))
    closing: list[Event] = [Beat(0, CONTROL)] if ticks_after else []
    return _h(
        Claim(
            0,
            own_process(0),
            Gate.FREE,
            Fresh(),
            None,
            Clock.LOADED,
            Fold.AT_DRAIN,
            CutSave.LANDED,
        ),
        opening,
        StopRequest("x0"),
        Emit(0, VALUE, Stamp.METRIC),
        Finish(0, True, TICKED if ticks_after else Undated.OWN_LAST),
        ThirdPartyStop(0, Report(None), None, NULL, Honors.UNDISCHARGED),
        closing,
        Exit(0, False, None, YIELDED, honors, discards=False),
        Halt(0, Exited(0)),
    )


def test_case_2_is_any_next_read_after_the_drain() -> None:
    """E2's case 2 (``CLEARED_AFTER_DRAIN``): the stop is cleared between the
    commit's native drain and the worker's next 0.3.0 read, its next tick in
    either loop order, or its exit. A ticks-only exit reads nothing, so with no
    tick after the clearing the stop is one that exit did not list, case 1."""
    for h in (
        _cleared_after_drain(tick_first=False, ticks_after=True, honors=Drain.EXIT),
        _cleared_after_drain(tick_first=True, ticks_after=False, honors=Drain.EXIT),
    ):
        assert describe(h)[-1] == "c0:exit extendable <- c0@0 [] honors x0"
        assert [c.case for c in truth(h).costs] == [Case.CLEARED_AFTER_DRAIN]
    h = _cleared_after_drain(tick_first=True, ticks_after=False, honors=Drain.TICKS)
    assert [c.case for c in truth(h).costs] == [Case.TICKS_EXIT]


def test_value_claim() -> None:
    assert "c1@2 <- c0@1 [v@2]" in describe(VALUE_CLAIM_SUCCESSORS)
    assert "c1@2 <- c0@1 []" in describe(VALUE_CLAIM_DISPLACED)
    assert "c1@2 <- c0@1 [v@2]" in describe(VALUE_CLAIM_EXITED_SUCCESSORS)
    assert "c1@2 <- c0@1 []" in describe(VALUE_CLAIM_TEARDOWN)
    # outside §7's site, which takes a claim no stopped named as gated dead:
    assert "c1@2 <- c0@1 [v@2]" in describe(VALUE_CLAIM_DEAD_UNNAMED)
    assert describe(VALUE_CLAIM_TAKEN_OVER)[3:6] == [
        "c0 died lost",
        "c1 starts from c0@1",
        "c1@2 <- c0@1 []",
    ]


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
    # the native counterpart discarded the open iteration: torn, a truth no §7
    # built-in gives a clean stopped
    assert describe(FINAL_OPEN_DISCARDED)[-1] == "c0:exit torn <- c0@2 null"
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
    """The golden's two readings, and a third: a release of the live claim.

    The worker took x0 in after the clearing (it folds at its claim), and its
    0.3.0 exit honours it. The withdrawal stands at its answer (§7, E2), before
    every native drain of the worker, so its native exit omits x0: E2's third
    stated cost, which E2 names with this golden. Under the release, the
    worker's own completed stopped is fenced, and its ``honored`` is a second
    answer, of an id the release's withdrawal already withdrew: E2 writes none
    for it ("not already withdrawn")."""
    assert describe(LATER_COMPLETED_FINISHED)[-3:] == [
        "withdraw x0",
        "c0@4 <- c0@3 [v@4]",
        "c0:exit closed <- c0@4 []",
    ]
    assert describe(LATER_COMPLETED_YIELDED)[-2:] == [
        "withdraw x0",
        "c0:exit closed <- c0@3 [v@4]",
    ]
    for h in (LATER_COMPLETED_FINISHED, LATER_COMPLETED_YIELDED):
        assert [c.case for c in truth(h).costs] == [Case.ANSWERED_AFTER_CLAIM]
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
    # a takeover writes the same report; the live worker, fenced, writes no node
    assert describe(SUCCESSOR_TOOK_OVER) == describe(SUCCESSOR_PROVED_DEAD)


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
    # D2: the successor resumed from a commit whose beat follows its claim
    assert "c1 starts from c0@2" in describe(DISPLACED_SAVED_FIRST)
    # D3, §6's rule on a synthetic writer: the successor resumed from an
    # extendable exit, its post-loop value included
    assert describe(SYNTHETIC_POST_EXIT_SAVE)[4:7] == [
        "c0:exit extendable <- c0@2 [v@2]",
        "c1 starts from c0:exit",
        "c1@3 <- c0:exit [v@3]",
    ]


# ----- the goldens' truths -------------------------------------------------------------

GOLDEN_TRUTHS: dict[str, list[str]] = {
    "DISPLACED": [
        "c0@0 <- root [loss@0]",
        "c0@1 <- c0@0 []",
        "c0 died lost",
        "c1 starts from c0@1",
        "c1@2 <- c0@1 []",
        "c1@3 <- c1@2 [loss@3]",
        "c1:exit closed <- c1@3 []",
    ],
    "ABOVE": ["c0 died lost", "c1@2 <- c0@1 []", "c1:exit closed <- c1@2 []"],
    "DISPLACEMENT_PARENT": [
        "c1@2 <- c0@1 [loss@2]",
        "c1:exit extendable <- c1@2 []",
        "c2 starts from c1@2",
    ],
    "COMMITLESS": [
        "c1 starts from c0@1",
        "c1:exit extendable <- c0@1 []",
        "c2 starts from c0@1",
    ],
    "RESUMED_PAST_AN_UNRECORDED_STEP": [
        "c0@2 <- c0@1 []",
        "c0 died errored",
        "c1 starts from c0@2",
    ],
    "RESUMED_PAST_A_PREEMPTED_EXIT": [
        "c0:exit extendable <- c0@1 [] honors preempt",
        "c1 starts from c0@1",
    ],
    "LOADING_RETRY_BESIDE_A_DISPLACED_WORKER": [
        "c0 died lost",
        "c1 starts from c0@1",
        "c1:exit torn <- c0@1 null",
    ],
    "RESUMED_FROM_A_STEP_COMMITTED_AFTER_THE_CLAIM": [
        "c0 died lost",
        "c1 starts from root",
        "c1@3 <- root []",
    ],
    "DOUBLY_DISPLACED": [
        "c1 starts from c0@0",
        "c1@1 <- c0@0 []",
        "c1 died lost",
        "c2 starts from root",
        "c2@4 <- root []",
    ],
    "RESUMED_FROM_A_TICK_SKIPPED_AFTER_THE_CLAIM": ["c1 starts from root"],
    "RESUMED_FROM_A_TICK_SKIPPED_ACROSS_THE_CLAIM": [
        "c0@2 <- c0@1 []",
        "c1 starts from c0@2",
    ],
    "RESUMED_ON_AN_OLDER_CLAIMS_STEP": ["c1 died lost", "c2 starts from c0@2"],
    "LOADING": [
        "c0@0 <- root [status@0, loss@0]",
        "c1@2 <- c0@1 [status@0, loss@2]",
    ],
    "SKIPPED": [
        "c0@2 <- c0@1 [loss@2, acc@2]",
        "c0@4 <- c0@3 []",
        "c0@5 <- c0@4 [loss@5]",
    ],
    "POST_LOOP": ["c0:exit closed <- c0@1 [final_eval@1]"],
    "TRAILING": ["c0@2 <- c0@1 [loss@2]", "c0:exit closed <- c0@2 []"],
    "PREEMPTED": ["c0@1 <- c0@0 [loss@1]", "c0:exit extendable <- c0@1 []"],
    "ERRORED": ["c0:exit torn <- c0@0 null"],
    "RESUMED_PAST_A_LOADING_CRASH": [
        "c0@2 <- c0@1 [loss@2]",
        "c0:exit torn <- c0@2 null",
        "c1:exit torn <- c0@2 null",
        "c2 starts from c0@2",
    ],
    "RESUMED_BESIDE_A_REAL_BEAT": [
        "c0:exit torn <- c0@1 null",
        "c1@2 <- c0@1 [loss@2]",
        "c2 starts from c1@2",
    ],
    "LOADING_RETRY_RELEASED": [
        "c1 starts from c0@0",
        "c1 died errored",
        "c2 starts from c0@0",
    ],
    "LOADING_RETRY_UNENDED": [
        "c1 starts from c0@0",
        "c1 died lost",
        "c2 starts from c0@0",
    ],
    "LOADING_RETRY_AT_ITS_RESUME_STEP": [
        "c0@2 <- c0@1 [loss@2]",
        "c1 starts from c0@2",
        "c2 starts from c0@2",
    ],
    "BEATLESS_FULL_STEP": [
        "c1@2 <- c0@1 [loss@2, status@2]",
        "c1 died errored",
        "c2 starts from c1@2",
    ],
    "AFTER_STOPPED": ["c0@0 <- root [loss@0]", "c0:exit closed <- c0@0 []"],
    "AFTER_AN_ATTRIBUTED_DEATH": [
        "c0@1 <- c0@0 [loss@1]",
        "terminated L1 Killed(signal=9)",
        "c0 died killed",
        "c1 starts from c0@1",
    ],
    "EXIT": ["c0:exit extendable <- c0@0 [] honors halt"],
    "LAUNCHED": ["c0:exit closed <- c0@0 []", "terminated L1 Exited(code=0)"],
    "WATERMARKED": ["c0@1 <- c0@0 []", "c0:exit extendable <- c0@3 [] honors halt"],
    "COOPERATIVE": ["c0:exit extendable <- c0@1 [] honors timeout"],
    "BACKSTOPPED": ["c0 died errored", "withdraw staged"],
    "LATE_BACKSTOP": [
        "c0:exit extendable <- c0@0 []",
        "terminated minted Exited(code=1)",
    ],
    "LATER_ERROR": ["c0:exit extendable <- c0@0 []"],
    "LATER_DEFAULT": ["c0:exit closed <- c0@0 [] honors halt"],
    "LATER_ERROR_HONORED": [
        "c0:exit extendable <- c0@0 [] honors halt",
        "withdraw halt",
    ],
    "CLAIMLESS": ["withdraw pre"],
    "CLAIMLESS_ERROR": ["withdraw pre"],
    "CLAIMLESS_BACKSTOP": ["terminated minted Killed(signal=9)", "withdraw pre"],
    "SECOND_BACKSTOP": ["c0 died errored", "terminated minted Exited(code=137)"],
    "BACKSTOP_ID_TAKEN": [
        "c0:exit extendable <- c0@0 [] honors backstop@5",
        "terminated minted Exited(code=1)",
    ],
    "BEATEN_AFTER": ["c0@1 <- c0@0 []"],
    "RECLAIM_REPORTED_LATER": ["c0 died preempted"],
    "RECLAIM_OF_10_23": ["c0 died errored"],
    "REPORTED_LATER_BOUNDARY": ["c0:exit extendable <- c0@0 []", "c1 died lost"],
    "ATTRIBUTED": ["terminated L1 Killed(signal=9)", "c0 died killed"],
    "WRAPPER": ["terminated L1 Exited(code=0)"],
    "ANOTHER_HOST": ["terminated L1 Exited(code=1)"],
    "LATE_REAP": [
        "c0 died lost",
        "c1 starts from c0@0",
        "terminated L1 Exited(code=1)",
        "c0 died errored",
        "terminated L2 Exited(code=0)",
    ],
    "TWO_CLAIMS_ONE_LAUNCH": [
        "c0 died lost",
        "c1 starts from c0@0",
        "terminated L1 Exited(code=0)",
        "c1 died lost",
    ],
}
"""§2's truth for each golden's story, line by line, in the native log's order."""

UNDYING = {"WRAPPER", "ANOTHER_HOST", "LATER_ERROR", "BEATEN_AFTER", "CLAIMLESS"}
"""Goldens whose claims no death report names: a wrapper's or another host's
process, an exit, a note, nothing claimed."""


def test_every_golden_has_its_truth() -> None:
    assert set(GOLDEN_TRUTHS) == set(GOLDENS)


@pytest.mark.parametrize("name", list(GOLDEN_TRUTHS))
def test_each_goldens_truth(name: str) -> None:
    lines = describe(GOLDENS[name][1])
    expected = GOLDEN_TRUTHS[name]
    assert [line for line in lines if line in expected] == expected
    if name in UNDYING:
        assert not any(" died " in line for line in lines)


DISPLACED_NODES: dict[str, list[str]] = {
    "DISPLACED": ["c0@0 <- root [loss@0]", "c0@1 <- c0@0 []"],
    "ABOVE": ["c0@0 <- root []", "c0@1 <- c0@0 []"],
    "DISPLACEMENT_PARENT": ["c0@0 <- root [loss@0]", "c0@1 <- c0@0 [loss@1]"],
    "LOADING_RETRY_BESIDE_A_DISPLACED_WORKER": ["c0@0 <- root []", "c0@1 <- c0@0 []"],
    "RESUMED_FROM_A_STEP_COMMITTED_AFTER_THE_CLAIM": [
        "c0@0 <- root []",
        "c0@1 <- c0@0 []",
    ],
    "DOUBLY_DISPLACED": [
        "c0@0 <- root []",
        "c0@1 <- c0@0 []",
        "c0@2 <- c0@1 []",
        "c1@1 <- c0@0 []",
    ],
    "RESUMED_FROM_A_TICK_SKIPPED_AFTER_THE_CLAIM": ["c0@0 <- root []"],
    "RESUMED_FROM_A_TICK_SKIPPED_ACROSS_THE_CLAIM": [
        "c0@0 <- root []",
        "c0@1 <- c0@0 []",
        "c0@2 <- c0@1 []",
    ],
    "RESUMED_ON_AN_OLDER_CLAIMS_STEP": ["c1@1 <- c0@0 []"],
    "TWO_CLAIMS_ONE_LAUNCH": ["c0@0 <- root []"],
}
"""Every node of each displacement golden's taken-over claims: the iterations
each finished before its takeover, and no exit node."""


@pytest.mark.parametrize("name", list(DISPLACED_NODES))
def test_no_node_after_the_fence(name: str) -> None:
    """D6: a displaced worker's beats, values and stopped after its successor's
    claim are no nodes (§4's fence); §7 commits such a beat and reads such a
    stopped as the worker's exit."""
    history = GOLDENS[name][1]
    taken = [
        ev.claim - 1
        for ev in history.events
        if isinstance(ev, Claim) and ev.gate is Gate.TAKEOVER
    ]
    prefixes = tuple(f"c{c}{mark}" for c in taken for mark in ("@", ":exit"))
    nodes = [line for line in describe(history) if line.startswith(prefixes)]
    assert nodes == DISPLACED_NODES[name]


# ----- Task 18: the target's own events ----------------------------------------------


def test_a_fenced_workers_honored_is_withdrawn_by_e2() -> None:
    """A fenced worker's own stopped is no exit node, and the stops it honours
    are withdrawn just after it (§7, E2). In the release order the successor's
    exit honours nothing, as 0.3.0's does. In the takeover order the successor,
    an at-claim writer, took x0 in after the answer, which its claim-time fold
    did not see: the withdrawal precedes its native drains, and its native exit
    omits x0, E2's third stated cost."""
    assert describe(RELEASE_ORDER)[2:] == [
        "c0 died lost",
        "withdraw x0",
        "c1 starts from c0@0",
        "c1@1 <- c0@0 [v@1]",
        "c1:exit extendable <- c1@1 []",
    ]
    assert truth(RELEASE_ORDER).costs == ()
    assert describe(TAKEOVER_ORDER)[2:] == [
        "c0 died lost",
        "c1 starts from c0@0",
        "withdraw x0",
        "c1@1 <- c0@0 [v@1]",
        "c1:exit extendable <- c1@1 []",
    ]
    (cost,) = truth(TAKEOVER_ORDER).costs
    assert cost.case is Case.ANSWERED_AFTER_CLAIM


def test_an_exit_that_lists_only_its_ticks_reads_is_e2s_first_cost() -> None:
    """A writer whose exit honours only what its ticks drained
    (``Drain.TICKS``) omits a stop that arrived after its last tick. No stop
    moves (§7, E2): the native exit drains it (§4, the exit's step 1) and
    honours it, E2's first stated cost."""
    h = _h(
        _claim(0, Fresh()),
        run(0, [0], SAVES),
        StopRequest("x0"),
        Exit(0, False, None, YIELDED, Drain.TICKS, discards=False),
        Halt(0, Exited(0)),
    )
    assert render(h).honored[-2] == ()
    assert describe(h)[-1] == "c0:exit extendable <- c0@0 [] honors x0"
    (cost,) = truth(h).costs
    assert cost.case is Case.TICKS_EXIT


def test_a_later_exit_omits_a_stop_an_earlier_one_discharged() -> None:
    """E2's case 4 (``DISCHARGED_EARLIER``): C0's native exit discharges x0
    under case 1, so C1, whose tick names x0 in its watermark
    (``consumed_seq: 4``) and whose 0.3.0 exit honours it, no longer honours it
    natively."""
    h = _h(
        _claim(0, Fresh()),
        run(0, [0], SAVES),
        StopRequest("x0"),
        Exit(0, False, None, YIELDED, Drain.TICKS, discards=False),
        Halt(0, Exited(0)),
        _claim(1, FromSave(SaveOf(0, 0))),
        run(1, [1], SAVES),
        _preempted(1),
    )
    assert json.loads(render(h).rows[7].body)["consumed_seq"] == 4
    assert describe(h)[2:] == [
        "c0:exit extendable <- c0@0 [] honors x0",
        "c1 starts from c0@0",
        "c1@1 <- c0@0 [v@1]",
        "c1:exit extendable <- c1@1 []",
    ]
    assert [c.case for c in truth(h).costs] == [
        Case.TICKS_EXIT,
        Case.DISCHARGED_EARLIER,
    ]


def test_the_reread_histories() -> None:
    """The re-review's histories, where the withdrawn E3 crossed another
    worker's read. No stop moves, so every watermark stays true (§7, E2). A and
    B: the omitting exit honours x0 natively (case 1). J: so does C1's, before
    C2 claims, so C2, whose 0.3.0 exit honours x0, omits it natively because C1
    discharged it (case 4), whatever the third party's answer after C2's claim
    would also cause (case 3): the earlier cause names it."""
    assert describe(REREVIEW_A)[-1] == "c1:exit torn <- c0@0 null honors x0"
    assert describe(REREVIEW_B)[-1] == "c1:exit extendable <- c0@0 [] honors x0"
    for h in (REREVIEW_A, REREVIEW_B):
        assert [c.case for c in truth(h).costs] == [Case.TICKS_EXIT]
    assert describe(REREVIEW_J)[5:] == [
        "c1:exit extendable <- c1@1 [] honors x0",
        "c2 starts from c1@1",
        "withdraw x0",
        "c2@2 <- c1@1 [v@2]",
        "c2:exit extendable <- c2@2 []",
    ]
    assert [c.case for c in truth(REREVIEW_J).costs] == [
        Case.TICKS_EXIT,
        Case.DISCHARGED_EARLIER,
    ]


def test_the_writer_fact_cut_save() -> None:
    """MIDSAVE: a pass cut after its save landed finished under ``landed`` and
    ``save-start``, its commit holding the values its pass wrote before the cut,
    and the save naming it; under ``closed`` the save names no node, and the
    next claim starts from none (task-17-convention-table.md, cut-save)."""
    assert describe(MIDSAVE_LANDED)[3:6] == [
        "c0@2 <- c0@1 [v@2]",
        "c0 died lost",
        "c1 starts from c0@2",
    ]
    closed = describe(MIDSAVE_CLOSED)
    assert "c1 starts from root" in closed
    assert not any(line.startswith("c0@2") for line in closed)
    # a save that never landed finishes its iteration only under save-start, on
    # a branch no claim resumes from
    assert "c0@2 <- c0@1 []" in describe(UNLANDED_SAVE_START)  # its status a register
    assert not any(line.startswith("c0@2") for line in describe(UNLANDED_LANDED))


def test_an_exit_that_cuts_the_pass_after_its_save() -> None:
    """The exit's kind governs (the controller's ruling on concern (c)). Under
    ``closed`` the save did not finish the pass, and an exit that keeps the open
    iteration holds its state, so the save names that exit, as a save just
    after it would (§6, "Save"; A17); one that discards it names nothing. Under
    ``landed`` and ``save-start`` the cut commits the iteration first."""
    assert describe(_midsave_exit(CutSave.CLOSED, discards=False))[3:6] == [
        "c0:exit extendable <- c0@1 [v@2]",
        "c1 starts from c0:exit",
        "c1@3 <- c0:exit [v@3]",
    ]
    assert describe(_midsave_exit(CutSave.CLOSED, discards=True))[3:5] == [
        "c0:exit torn <- c0@1 null",
        "c1 starts from root",
    ]
    for cut_save in (CutSave.LANDED, CutSave.SAVE_START):
        assert describe(_midsave_exit(cut_save, discards=False))[3:6] == [
            "c0@2 <- c0@1 [v@2]",
            "c0:exit extendable <- c0@2 []",
            "c1 starts from c0@2",
        ]
        with pytest.raises(NotConforming, match="discards"):
            walk(_midsave_exit(cut_save, discards=True))


def test_a_cut_commit_is_dated_at_the_cut() -> None:
    """A commit's ``t`` is at or after every value it holds (§3), so a commit a
    cut makes is dated at the cut, never at its save: the end-time fork's
    bounds there, the claim's newest record before it (the metric written after
    the save's tick), or the log's next record (the next claim's started)."""
    rows = render(MIDSAVE_LANDED).rows
    (cut,) = [c for c in truth(MIDSAVE_LANDED).lineage.commits if c.node.nth == 2]
    tick = max(r.seq for r in rows if r.topic == "lifecycle.heartbeat" and r.seq < 12)
    assert cut.t == max(cut.values) > tick
    later = _midsave_ended(Undated.NEXT)
    (cut,) = [c for c in truth(later).lineage.commits if c.node.nth == 2]
    started = [r.seq for r in render(later).rows if r.topic == "lifecycle.started"]
    assert cut.t == started[1]


def _midsave_ended(end: Undated) -> History:
    """MIDSAVE_LANDED, with its passes' undated ends ``end``."""
    shape = replace(MYCOOC, ended=end)
    return _h(
        _claim(0, Fresh()),
        run(0, [0, 1], shape),
        iteration(0, Step(2, shape), cut=Cut.AFTER_SAVE),
        _killed(0),
        _claim(1, FromSave(SaveOf(0, 2))),
        run(1, [3], shape),
        _completes(1),
    )


def test_every_cut_runs_in_mid_saves() -> None:
    """``mid-saves`` cuts a pass after its save by each event that can: a kill,
    the worker's own exit, a takeover's fence and a release's fence."""
    cutters: set[str] = set()
    for h in _histories(FAMILIES["mid-saves"]):
        for ev, d in zip(h.events, walk(h).derived):
            if d.cut is not None:
                cutters.add(type(ev).__name__)
    assert cutters == {"Halt", "Exit", "Claim", "ThirdPartyStop"}


def test_a_commits_t_is_when_its_iteration_ended() -> None:
    """A commit's ``t`` (§3, lifecycle-v0.6) is its own tick's where one dates it;
    else the history's ``Ended`` says: its claim's newest record before it, or
    the log's next record (§7, the end-time fork)."""
    rows = render(GAP_ITERATION).rows
    beats = [r.seq for r in rows if r.topic == "lifecycle.heartbeat"]
    early = [c.t for c in truth(GAP_ITERATION).lineage.commits]
    late = [c.t for c in truth(GAP_ITERATION_LATE).lineage.commits]
    assert early == [beats[0], beats[0], beats[1]]
    assert late == [beats[0], beats[1], beats[1]]


def test_e1_kinds_and_texts() -> None:
    """E1 (§7): each death report the step writes says ``migrated:``, its source's
    kind and that record's own evidence; the kind is in the lineage."""
    cases = [
        (STOPPED_AFTER_ITS_REAP, Source.REAP, "migrated: reap: signal 9"),
        (SUCCESSOR_PROVED_DEAD, Source.GATE, "migrated: gate: local://sim/1000"),
        (BEATEN_PAST_RELEASED, Source.STOPPED, "migrated: stopped: job 7 FAILED"),
        (TPS_RELEASE_OF_DEAD, Source.STOPPED, "migrated: stopped"),
        (NAMED["golden LATE_REAP"], Source.REAP, "migrated: reap: exit 1"),
    ]
    for history, source, text in cases:
        t = truth(history)
        assert any(e.died.source is source and e.text == text for e in t.evidence), text
        assert {d.source for d in t.lineage.deaths} >= {source}


def test_d78b72f1_in_mycoocs_record_order() -> None:
    """The named history renders the store copy's log, every record of it, and
    its truth holds under every cut_save value: every pass closed
    (task-17-convention-table.md, "d78b72f1 does not depend on it")."""
    store = _d78_store_copy()
    assert len(store) == 4243
    assert D78_REAL.born is Born.V0_2_0  # as the real log was
    assert hashlib.sha256(json.dumps(store).encode()).hexdigest() == D78_DIGEST
    rendered = [
        (
            r.topic,
            r.name,
            json.loads(r.body).get("step") if r.topic in _STEPPED else None,
        )
        for r in render(D78_REAL).rows
    ]
    assert rendered == store
    truths = {cut_save: truth(_d78_real(cut_save)).lineage for cut_save in CutSave}
    assert len(set(truths.values())) == 1
    lines = describe(D78_REAL)
    for line in ("c1 starts from c0@181", "c2 starts from c1@182"):
        assert line in lines
    assert any(line.startswith("c0@181 <- c0@180 [") for line in lines)
    # status is a register by mycooc's word: no node holds one (F3)
    assert not any("status@" in line for line in lines)


_STEPPED = ("value", "lifecycle.heartbeat")


def test_a_synthetic_writer_version_that_changes_mid_root() -> None:
    """A synthetic writer, not mycooc's: claim 0's old version saved after its
    exit, so claim 1 resumes from that extendable exit; claim 1's new version
    saved before its exit, so claim 2 resumes from its last commit (§6, "Save";
    Review Focus 9)."""
    lines = describe(SYNTHETIC_VERSION_CHANGE)
    assert "c1 starts from c0:exit" in lines
    assert "c2 starts from c1@5" in lines


def test_slot_holds_three_claims_and_the_load_constraint() -> None:
    """SLOT's family (the critique's P1, P2): three claims; ``loads_newest`` says
    which histories satisfy ``load=newest``. The critique's SLOT history is in
    it, and every history with its log that satisfies the constraint resumes
    C3 from C2's 3; without it, C3 may resume from C1's."""
    family = list(slot(beats=4, run_on=1))
    assert len(family) < BOUND
    assert SLOT in family and SLOT_OLDER in family
    assert loads_newest(SLOT) and not loads_newest(SLOT_OLDER)
    rows = render(SLOT).rows
    starts = {
        describe(h)[-4] for h in family if render(h).rows == rows and loads_newest(h)
    }
    assert starts == {"c2 starts from c1@3"}
    assert "c2 starts from c0@3" in describe(SLOT_OLDER)


def test_displaced_workers_keep_saving_and_strays_stay_out() -> None:
    """DISPLACED_SAVES: a displaced worker's save after its takeover names no
    node (§4's fence), so a claim that loads it starts from none; a stray value,
    written by a process before it claims, is in no commit."""
    resumed_from_nothing = stray_committed = False
    for h in _histories(FAMILIES["displaced-saves"]):
        t = truth(h)
        rendered = render(h)
        strays = {
            seq for ev, seq in zip(h.events, rendered.seqs) if isinstance(ev, Stray)
        }
        committed = {v for c in t.lineage.commits for v in c.values}
        stray_committed |= bool(strays & committed)
        for ev in h.events:
            if (
                isinstance(ev, Claim)
                and isinstance(ev.origin, FromSave)
                and ev.origin.save.claim == 0
                and ev.claim == 2
            ):
                origin = rendered.walk.origins[2]
                resumed_from_nothing |= origin is None
    assert resumed_from_nothing and not stray_committed


# ----- the rest of the type ----------------------------------------------------------


def test_a_rewind_restates_the_position() -> None:
    """An in-claim rewind (§7, M9 refuses one: a heartbeat at or below a step
    its claim already beat) is a native ``lifecycle.rewind``; the pending values
    it drops stay outside every lineage."""
    h = _h(
        _claim(0, Fresh()),
        run(0, range(3), SAVES),
        Emit(0, VALUE, Stamp.METRIC),
        Rewind(0, SaveOf(0, 0)),
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
        _claim(0, Fresh()),
        Emit(0, "status", Stamp.STEPLESS),
        Beacon(0, CONTROL),
        run(0, [0], SAVES),
        _completes(0),
    )
    heartbeat = render(h).rows[2]
    assert json.loads(heartbeat.body)["step"] is None
    assert describe(h)[1:] == ["c0@0 <- root [v@0]", "c0:exit closed <- c0@0 []"]


# ----- what the model refuses ----------------------------------------------------------


@pytest.mark.parametrize(
    "events, rule",
    [
        (  # the gate's verdict is stated: a claim over a held, live claim is a takeover
            [_claim(0, Fresh()), _claim(1, Fresh())],
            "a takeover",
        ),
        (  # and a takeover takes a held claim over, never an ended one
            [_claim(0, Fresh()), *_preempted(0), _takeover(1, Fresh())],
            "no takeover",
        ),
        (  # a worker's stopped is its halt
            [
                _claim(0, Fresh()),
                _exit(0, completed=False, error=None, final=YIELDED),
                Beat(0, CONTROL),
            ],
            "a beat ticks an iteration",
        ),
        (  # completed carries no error
            [_claim(0, Fresh()), _exit(0, completed=True, error="boom", final=YIELDED)],
            "carries no error",
        ),
        (  # discarding is for a clean exit inside an open iteration
            [
                _claim(0, Fresh()),
                Exit(0, False, None, YIELDED, Drain.EXIT, discards=True),
            ],
            "only a clean exit inside an open iteration",
        ),
        (  # run-absolute steps: a resumed claim's labels rise above its state
            [
                _claim(0, Fresh()),
                *run(0, [0, 1], SAVES),
                _killed(0),
                _claim(1, FromSave(SaveOf(0, 1))),
                Begin(1, 1),
            ],
            "below the clock's floor",
        ),
        (  # a process ends once
            [
                _claim(0, Fresh()),
                _killed(0),
                Claim(
                    1,
                    own_process(0),
                    Gate.FREE,
                    Fresh(),
                    None,
                    Clock.LOADED,
                    Fold.AT_CLAIM,
                    CutSave.LANDED,
                ),
            ],
            "has ended",
        ),
        (  # a reap follows its process's end
            [
                Launched("L", own_process(0)),
                Claim(
                    0,
                    own_process(0),
                    Gate.FREE,
                    Fresh(),
                    "L",
                    Clock.LOADED,
                    Fold.AT_CLAIM,
                    CutSave.LANDED,
                ),
                Reaped("L", Exited(0)),
            ],
            "reaped while it lives",
        ),
        (  # the run is at rest
            [_claim(0, Fresh())],
            "never halts",
        ),
    ],
)
def test_the_model_refuses_what_no_conforming_writer_does(
    events: list[Event], rule: str
) -> None:
    with pytest.raises(NotConforming, match=rule):
        walk(History(tuple(events), Born.V0_3_0))


def test_a_fenced_workers_save_of_an_earlier_node_lands() -> None:
    """m3: a released worker's save after its tick, of the node it committed
    before the release, lands, since natively the save runs inside that
    commit; a later claim may load it."""
    h = _h(
        _claim(0, Fresh()),
        Begin(0, 0),
        Finish(0, False, Undated.OWN_LAST),
        _released(0, Outcome.LOST),
        Beat(0, CONTROL),
        Save(0),
        _killed(0),
        _claim(1, FromSave(SaveOf(0, 0))),
        _killed(1),
    )
    assert describe(h)[-1] == "c1 starts from c0@0"


def test_a_save_of_no_node_is_accepted_and_names_none() -> None:
    """m7: a save after a torn exit is conforming 0.3.0 and names no node, so a
    claim that loads it starts from no node: state no node of the run holds
    (§2, "The claim"; §3, ``resume_from``)."""
    torn: list[Event] = [
        _claim(0, Fresh()),
        *_open(0, 0, VALUE),
        _exit(0, completed=False, error="boom", final=YIELDED),
        Save(0),
        Halt(0, Exited(1)),
    ]
    walk(_h(torn))
    loaded = _h(torn, _claim(1, FromSave(ExitSave(0))), _killed(1))
    assert "c1 starts from root" in describe(loaded)


def test_a_refused_prefix_is_pruned() -> None:
    """m5: a space whose stride 0 labels an iteration below its floor yields
    only conforming histories, and never raises."""
    widened = replace(RESUMES, strides=(0, 1))
    found = list(histories(claims=2, steps=1, stops=0, space=widened))
    assert found and all(conforms(h) for h in found)


def test_a_fenced_workers_later_save_names_no_node() -> None:
    """A worker fenced by a release or a takeover makes no node after it (§4),
    so a claim that loaded its later save loaded state no node of the run holds,
    and starts from no node, as one loading a checkpoint from outside does."""
    taken = [
        _claim(0, Fresh()),
        *run(0, [0], SAVES),
        _takeover(1, FromSave(SaveOf(0, 0))),
        *run(0, [1], SAVES),
        _killed(0),
        _killed(1),
    ]
    loaded = describe(_h(taken, _claim(2, FromSave(SaveOf(0, 1))), _killed(2)))
    outside = describe(_h(taken, _claim(2, FromOutside(1)), _killed(2)))
    assert "c2 starts from root" in loaded
    assert loaded == outside


# ----- the Task 19 gaps ----------------------------------------------------------------


def _resumed_zero(c: int, origin: Origin) -> Claim:
    """Claim ``c``, loading ``origin``, whose clock reads 0 before its first
    iteration (mycooc's counter)."""
    return Claim(
        c,
        own_process(c),
        Gate.FREE,
        origin,
        None,
        Clock.ZERO,
        Fold.AT_CLAIM,
        CutSave.LANDED,
    )


def test_a_save_records_its_states_step_not_its_clock() -> None:
    """F4: under ``Clock.ZERO`` a save before any iteration is of the loaded
    state, at its step, whatever the clock reads. So a later claim that loads it
    labels above that step, and the review's reproduction, labelled 1 over a
    loaded state at 2, is refused; an exit's save is of the same state."""
    first: list[Event] = [_claim(0, Fresh()), *run(0, [0, 1, 2], SAVES), _killed(0)]
    saved: list[Event] = [_resumed_zero(1, FromSave(SaveOf(0, 2))), Save(1), _killed(1)]
    assert loadable(_h(first, saved).events)[SaveOf(1, 2)] == 2
    with pytest.raises(NotConforming, match="below the clock's floor 3"):
        walk(_h(first, saved, _claim(2, FromSave(SaveOf(1, 2))), Begin(2, 1)))
    good = _h(
        first,
        saved,
        _claim(2, FromSave(SaveOf(1, 2))),
        run(2, [3], SAVES),
        _completes(2),
    )
    assert "c2 starts from c0@2" in describe(good)
    exited: list[Event] = [
        _resumed_zero(1, FromSave(SaveOf(0, 2))),
        _exit(1, completed=False, error=None, final=YIELDED),
        Save(1),
        Halt(1, Exited(0)),
    ]
    assert loadable(_h(first, exited).events)[ExitSave(1)] == 2


def test_the_counter_convention() -> None:
    """F1: mycooc's ``final_step`` is its counter, the last iteration it
    finished, else what its clock read before its first: 0 for a resumed claim
    under ``Clock.ZERO`` that finished none, where ``finished`` writes null."""

    def final(convention: FinalStep, *passes: Event) -> Any:
        h = _h(
            _claim(0, Fresh()),
            run(0, [0, 1], SAVES),
            _killed(0),
            _resumed_zero(1, FromSave(SaveOf(0, 1))),
            *passes,
            Exit(1, False, None, convention, Drain.EXIT, discards=False),
            Halt(1, Exited(0)),
        )
        return json.loads(render(h).rows[-1].body)["final_step"]

    assert final(FinalStep.COUNTER) == 0
    assert final(FinalStep.FINISHED) is None
    assert final(FinalStep.COUNTER, *run(1, [2], SAVES)) == 2


def test_a_stepped_register_is_held_by_no_node() -> None:
    """F3: a value with a step that its writer keeps outside every lineage
    (mycooc's ``status``) is a register: it renders at its clock, and no node
    holds it, where a metric at the same step is committed."""
    h = _h(
        _claim(0, Fresh()),
        Begin(0, 0),
        Emit(0, STATUS, Stamp.REGISTER),
        Emit(0, VALUE, Stamp.METRIC),
        Finish(0, True, Undated.OWN_LAST),
        _completes(0),
    )
    assert [json.loads(r.body)["step"] for r in render(h).rows[1:3]] == [0, 0]
    assert describe(h)[1] == "c0@0 <- root [v@0]"


def test_a_log_born_in_0_2_0_carries_positional_names() -> None:
    """Ruling 4: the 0.2.0 -> 0.3.0 step filled every ``stopped``'s ``honored``
    with the stops since the previous ``stopped``, and every heartbeat's and
    ``stopped``'s ``claim_seq`` with the latest claim before it. A worker
    displaced by a takeover then beats and stops naming its successor's claim.
    The truth is the history's: its records are no node (§4's fence)."""
    taken = _h(
        _claim(0, Fresh()),
        run(0, [0], SAVES),
        StopRequest("x0"),
        _takeover(1, FromSave(SaveOf(0, 0))),
        run(0, [1], SAVES),
        _preempted(0),
        run(1, [1], SAVES),
        _preempted(1),
    )
    born = replace(taken, born=Born.V0_2_0)
    bodies = {r.seq: json.loads(r.body) for r in render(born).rows}
    claim1, displaced_beat, displaced_stop = 5, 7, 8
    assert bodies[displaced_beat]["claim_seq"] == claim1
    assert bodies[displaced_stop]["claim_seq"] == claim1
    assert bodies[displaced_stop]["honored"] == ["x0"]
    assert (
        render(taken).rows[displaced_beat - 1].body
        != render(born).rows[displaced_beat - 1].body
    )
    assert describe(born)[:3] == describe(taken)[:3]


def test_an_inferred_input_list_is_e2s_fifth_cost() -> None:
    """E2's fifth case (``INFERRED_INPUT_LIST``, the controller's ruling): in a
    log born in 0.2.0 an exit's list is the earlier step's positional inference.
    Here a note lists a stop the worker's tick had already taken in, so the
    exit's positional list omits it, and the native exit honours it, held at a
    heartbeat (§4). In a log born in 0.3.0 the writer's own list names it, and
    there is no cost."""
    h = _h(
        _claim(0, Fresh()),
        StopRequest("x0"),
        run(0, [0], SAVES),
        ThirdPartyStop(0, Report(None), "note", PROGRESS, Honors.NONE),
        _preempted(0),
    )
    assert truth(h).costs == ()
    (cost,) = truth(replace(h, born=Born.V0_2_0)).costs
    assert (cost.request_id, cost.case) == ("x0", Case.INFERRED_INPUT_LIST)


def _phases(h: History) -> Iterator[tuple[Event, str]]:
    """Each worker event with its claim's phase before it: loading, iterating,
    between or exited."""
    phase: dict[int, str] = {}
    for ev in h.events:
        if isinstance(ev, Claim):
            phase[ev.claim] = "loading"
            continue
        if isinstance(ev, (Begin, Emit, Beat, Beacon, PassSave, Finish, Save, Exit)):
            yield ev, phase[ev.claim]
            if isinstance(ev, Begin):
                phase[ev.claim] = "iterating"
            elif isinstance(ev, Finish):
                phase[ev.claim] = "between"
            elif isinstance(ev, Exit):
                phase[ev.claim] = "exited"


def test_the_blind_spots_are_reached() -> None:
    """Task 19's blind spots, each reached by a family: saves before the first
    iteration, between iterations and of partial state; a claimless note and
    reap; a reap reported on a live claim; a counter ``final_step``; stepped
    registers; a label that skips; a takeover of a halted worker; and rewinds."""
    found: set[str] = set()
    for name in ("save-points", "third-party-acts"):
        for h in _histories(FAMILIES[name]):
            live: set[int] = set()
            halted: set[int] = set()
            for ev in h.events:
                if isinstance(ev, Claim):
                    live.add(ev.claim)
                    if ev.gate is Gate.TAKEOVER and ev.claim - 1 in halted:
                        found.add("takeover=dead")
                elif isinstance(ev, Halt):
                    live.discard(ev.claim)
                    halted.add(ev.claim)
                elif isinstance(ev, ThirdPartyStop) and isinstance(ev.intent, Report):
                    if ev.names is None:
                        found.add(
                            "unnamed " + ("note" if ev.intent.reap is None else "reap")
                        )
                    elif ev.intent.reap is not None and ev.names in live:
                        found.add("live reap")
            for ev, phase in _phases(h):
                if isinstance(ev, Save):
                    found.add(f"save {phase}")
                elif isinstance(ev, Emit) and ev.stamp is Stamp.REGISTER:
                    found.add("stepped register")
                elif isinstance(ev, Exit) and ev.final_step is FinalStep.COUNTER:
                    found.add("counter")
                elif isinstance(ev, Begin) and ev.step == 1 and phase == "loading":
                    origin = next(
                        e.origin
                        for e in h.events
                        if isinstance(e, Claim) and e.claim == ev.claim
                    )
                    found |= {"skip"} if isinstance(origin, Fresh) else set()
    assert found >= {
        "save loading",
        "save between",
        "save iterating",
        "unnamed note",
        "unnamed reap",
        "live reap",
        "counter",
        "stepped register",
        "skip",
        "takeover=dead",
    }, found


def test_rewinds_are_reached_and_no_record_shows_one() -> None:
    """``Rewind`` in enumeration: a worker reloads the root or a save between
    iterations. A rewind to its newest save writes the log no rewind writes, so
    the rewind is a fork no record decides."""
    hs = list(rewinds(passes=3))
    assert len(hs) < BOUND and len(set(hs)) == len(hs)
    assert any(isinstance(ev, Rewind) for h in hs for ev in h.events)
    lineages: dict[tuple[Row, ...], set[Any]] = defaultdict(set)
    for h in hs:
        t = truth(h)
        assert violations(t) == ()
        lineages[render(h).rows].add(t.lineage)
    assert any(len(ls) > 1 for ls in lineages.values())


def test_a_third_partys_value_is_a_register_and_no_evidence() -> None:
    """(s): a third party writes a value on a held claim's log mid-claim
    (mycooc's orchestrator: ``completion_reason``, be8679020f32 seqs 552, 554
    and 556). It is a register: no node holds it. At the worker's label it
    writes the log a worker's own value writes, which a node holds, so only the
    classifier separates them; at any other label it moves no node. Reached in
    ``released-live``."""

    def pass_with(value: Event) -> History:
        return _h(
            _claim(0, Fresh()),
            Begin(0, 0),
            value,
            Finish(0, True, TICKED),
            Beat(0, CONTROL),
            _completes(0),
        )

    own = pass_with(Emit(0, VALUE, Stamp.METRIC))
    theirs = pass_with(ThirdPartyValue(VALUE, 0))
    assert render(own).rows == render(theirs).rows
    assert describe(own)[1] == "c0@0 <- root [v@0]"
    assert describe(theirs)[1] == "c0@0 <- root []"
    far = describe(pass_with(ThirdPartyValue(VALUE, 7)))
    assert far == describe(pass_with(ThirdPartyValue(VALUE, None))) == describe(theirs)
    assert any(
        isinstance(ev, ThirdPartyValue)
        for h in _histories(FAMILIES["released-live"])
        for ev in h.events
    )


def test_third_parties_stop_claims_something_else_ended(
    conforms_to_0_3_0: Callable[[Rendered], None],
) -> None:
    """``acts``' ``ended`` state (Task 20): a third party's stopped naming a
    claim that a release or a later claim ended first, as mycooc's backstop
    since 88425fa8 (a reap on the previous claim) and its repair script (a note
    after a malformed release) write. Every truth is well formed, every log
    conforms to the 0.3.0 schemas, and each act is reached in the state."""
    hs = list(ended_stops())
    assert len(hs) < BOUND and len(set(hs)) == len(hs)
    kinds: set[str] = set()
    for h in hs:
        assert violations(truth(h)) == ()
        conforms_to_0_3_0(render(h))
        for k, ev in enumerate(h.events):
            if not (isinstance(ev, ThirdPartyStop) and ev.names == 0):
                continue
            # the events run in log order: what ended claim 0 is an event before
            before = h.events[:k]
            ended = any(isinstance(e, Claim) and e.claim == 1 for e in before) or any(
                isinstance(e, ThirdPartyStop)
                and e.names == 0
                and isinstance(e.intent, Release)
                for e in before
            )
            if ended:
                kinds.add(
                    ("note" if ev.intent.reap is None else "reap")
                    if isinstance(ev.intent, Report)
                    else "release"
                )
    assert kinds == {"note", "reap", "release"}
