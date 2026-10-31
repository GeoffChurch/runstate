"""What the migration tests share beyond the golden logs (``migration_goldens.py``), by
public names, so that no test module imports another's private builders: the empty
history, the 0.3.0 -> 0.4.0 step tests' limits, consumer word and choosers, and the logs
several test modules build or read, the 0.3.0 reference Worker's among them."""

import functools
import json
from pathlib import Path

import runstate.migrations.v0_3_0_to_v0_4_0 as STEP
import runstate.migrations.v0_3_0_to_v0_4_0_facts as V
from runstate.migrations import Row
from runstate.migrations.ambiguity import (
    REFUSE,
    Assumptions,
    Chosen,
    History,
    Limits,
    Resolutions,
)
from tests.migration_goldens import _beat, _dated, _started, _stopped, _value, r
from tests.migration_sim.enumerate import SAVE_POINTS, histories
from tests.migration_sim.render import render

NO_HISTORY = History((), ())
"""A run with no manifest entry and no trace in the store: a run born where it is, or
copied in without its entries."""

LIMITS = Limits(cap=3000, budget=6_000_000, run_on=2)
"""The step tests' limits."""

CONSUMER = dict(
    stride=frozenset({V.Stride.NEXT}),
    loading=frozenset(),
    closing=frozenset({V.Closing.OTHER}),
    marker=V.every(V.Marker),
    values=frozenset({V.Phase.PASS}),
    loop_end=frozenset({V.LoopEnd.AFTER_CLOSE}),
    discards=frozenset({V.Discards.KEEPS}),
    cut_save=frozenset({V.CutSave.CLOSED}),
    save_at=frozenset({V.SaveAt.IN_PASS, V.SaveAt.AT_CLOSE}),
    save_cov=frozenset({V.Saved.SAVED}),
    leave_save=frozenset({V.Saved.SAVED}),
    load=frozenset({V.Load.FRESH, V.Load.NEWEST}),
    resume=frozenset({V.Resume.AFTER}),
    strays=frozenset(),
    born=frozenset({V.Birth.V0_3_0}),
)
"""A consumer's word on its own code, as a consumer of the reference Worker would give
it: every claim resumed at the loaded step plus one (``steps(start=k + 1)`` after
loading pass ``k``'s save, ``resume`` ``after``) or started at 0, no record written
before the first pass, no pass ends at a value, values only inside a pass, the loop left
after a close, each pass's completed state saved inside it or at its close, the newest
save or nothing loaded, and no other writer's values; whether a silent pass wrote a
value, unknown."""


def under(configuration, **choosers):
    """Resolutions: ``configuration`` stated, each projection's chooser as given, else
    ``refuse``."""
    picked = {
        p.name: choosers.get(p.name.replace("-", "_"), Chosen(None, REFUSE.chooser))
        for p in STEP.V0_3_0_to_V0_4_0.PROJECTIONS
    }
    return Resolutions(Assumptions(configuration, "test", LIMITS), picked)


CHOSEN = {
    "span": Chosen("fewest", STEP.FEWEST.chooser),
    "origin": Chosen("newest", STEP.NEWEST.chooser),
    "end_time": Chosen("lower", STEP.END_TIME.generic[0].chooser),
}
"""The step tests' choosers: span ``fewest``, origin ``newest``, end-time ``lower``."""


def preset(**word):
    """Resolutions under the reference preset, the consumer's word ``CONSUMER`` with its
    bodies ending a pass anywhere and ``word`` over it, and the ``CHOSEN`` choosers."""
    return under(
        STEP.reference(**{**CONSUMER, "closing": V.every(V.Closing), **word}), **CHOSEN
    )


_REFERENCE_WORKER_LOGS = (
    Path(__file__).parent
    / "fixtures"
    / "log-format-0.3.0"
    / "reference-worker-logs.json"
)


@functools.cache
def _reference_worker_logs():
    return json.loads(_REFERENCE_WORKER_LOGS.read_text())


def reference_worker_log(key: str) -> list[Row]:
    """A log the 0.3.0 reference Worker wrote, as rows (``created_at`` = ``float(seq)``,
    as the generators made them), from ``fixtures/log-format-0.3.0``. The 0.4.0 live
    layer replaces that Worker, so its logs were captured at 9b83be6 by a scratch
    script that drove it over a memory channel exactly as the generators these keys
    replace did, and that checked these rows equal theirs, key by key:

    - ``resumed-2``, ``resumed-5``: three passes, then a second claim resuming at that
      step; ``resumed-3-before-2``: resuming at 3 after first ``channel.send``-ing
      ``val_loss`` at step 2, the Worker's own route for a caller-clocked point
      (``emit`` raises before the first tick). Each pass emits ``loss``.
    - ``reference-done`` (completed), ``reference-plain`` (preempted),
      ``reference-stop`` (honouring a stop at pass 1), ``reference-error`` (raising in
      pass 2): four passes, each emitting ``loss`` (each claim's ``set("lr")`` is
      unsubscribed, so it leaves no record);
      ``reference-resume``, four passes then a second claim resuming at step 4.
    - ``sim-clean-run``, ``sim-kill-mid-step``, ``sim-resume``,
      ``sim-cooperative-stop``: the simulator's reference-Worker scenarios
      (``tests/test_migration_sim.py``), on the wall clock.

    Every claim's handle is the capturing process's, so ``resolve`` reads it by the
    host it runs on: ``sim-kill-mid-step``, the one log whose claim is still open, is
    never fed to ``migrate`` or ``is_live``. A body is the stored JSON text, byte for
    byte."""
    return [
        Row(seq, topic, name, request_id, body, float(seq))
        for seq, topic, name, request_id, body in _reference_worker_logs()[key]
    ]


def a_claim_then_another():
    """A claim's pass 0, its value and its tick, then another claim."""
    return _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "value", _value(0.9, 0, 2.0), name="loss"),
            r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 3.0)),
            r(4, "lifecycle.started", _started("local://n1/12", 4.0)),
        ]
    )


def a_watermark_on(stop_body):
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


def a_beacon_between_two_places():
    """The re-review's five records: pass 0 dated by its tick (seq 3); pass 1, which the
    exit's ``final_step`` begins, closes silently before the beacon at seq 4 or after
    it. The beacon is the worker's own record, which no other reading reads."""
    return [
        r(1, "lifecycle.started", _started("local://n/1", 1.0), t=1.0),
        r(2, "value", _value(1.0, 0, 2.0), name="loss", t=2.0),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 3.0), t=3.0),
        r(4, "lifecycle.heartbeat", _beat(None, 0, 1, 4.0), t=4.0),
        r(5, "lifecycle.stopped", _stopped(False, None, 1, 1, [], 5.0), t=5.0),
    ]


def six_records():
    """The review's six-record ``save-points`` log and the twelve histories that write
    it (four whose second claim resumes after the save's step, eight at it): two
    claims, the second's three step-0 values, then its tick at step 0."""
    by_log = {}
    for h in histories(claims=2, steps=1, stops=0, space=SAVE_POINTS):
        by_log.setdefault(render(h).rows, []).append(h)
    for rows, hs in by_log.items():
        if [x.topic for x in rows] == [
            "lifecycle.started",
            "value",
            "lifecycle.started",
            "value",
            "value",
            "lifecycle.heartbeat",
        ] and json.loads(rows[5].body) == {
            "step": 0,
            "consumed_seq": 0,
            "claim_seq": 3,
            "t": 6.0,
        }:
            return rows, hs
    raise AssertionError("the six-record log is gone from save-points")
