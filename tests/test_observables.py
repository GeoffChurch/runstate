"""The stateless observer plane (docs/specs/observables.md).

Pure folds log -> derived view, parametrized over the backends: the liveness
verdicts (peek_terminal -> RunResult, live_episode), the episode-boundary rule
(latest_episode), the step frontier (progress), and the value-plane register
projection (value_series).
"""

import pytest

from runstate.observables import (
    MalformedRecordError,
    RunResult,
    last_activity,
    latest_episode,
    live_demand,
    live_episode,
    peek_terminal,
    worker_completed,
    progress,
    undischarged_stops,
    value_series,
)
from runstate.vocabulary.handle import local_handle


def test_last_activity_never_reads_value_records(open_run):
    # value.t is the DATA plane's clock (present-nullable), a different concern:
    # freshness reads only the beacon/terminal records. The blind spot is
    # DOCUMENTED (observables.py) -- i.e. an accepted design decision, and this
    # is its trip-wire: "fixing" it must fail a test, not slip in.
    ch = open_run()
    ch.send({"value": 1.0, "step": 0, "t": 999.0}, topic="value", name="loss")
    assert last_activity(open_run()) is None


def test_last_activity_is_the_newest_dated_record(open_run):
    ch = open_run()
    assert last_activity(open_run()) is None  #                      nothing dated yet
    ch.send({"handle": "local://h/1", "t": 10.0}, topic="lifecycle.started")
    assert (
        last_activity(open_run()) == 10.0
    )  #                      a just-started run HAS an age
    ch.send({"step": 0, "consumed_seq": 0, "t": 20.0}, topic="lifecycle.heartbeat")
    ch.send({"step": 1, "consumed_seq": 0, "t": 35.0}, topic="lifecycle.heartbeat")
    assert last_activity(open_run()) == 35.0  #                      newest beacon
    ch.send(
        {"completed": True, "error": None, "final_step": 1, "t": 40.0},
        topic="lifecycle.stopped",
    )
    assert (
        last_activity(open_run()) == 40.0
    )  #                      max across the dated records


def test_last_activity_skips_a_junk_t_measurement_fold(open_run):
    # a measurement fold: a junk-typed t on the LATEST record of a topic is skipped
    # (that topic contributes nothing), not raised (tolerance split). last_activity reads
    # latest-per-topic, so it falls back to the other dated topics.
    ch = open_run()
    ch.send({"handle": "local://h/1", "t": 10.0}, topic="lifecycle.started")
    ch.send({"step": 0, "consumed_seq": 0, "t": "junk"}, topic="lifecycle.heartbeat")
    assert (
        last_activity(open_run()) == 10.0
    )  #                      junk beacon skipped, started stands


def test_none_while_running(open_run):
    ch = open_run()
    ch.send({"step": 0, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
    assert peek_terminal(open_run()) is None


def test_completed(open_run):
    open_run().send(
        {"completed": True, "error": None, "final_step": 500, "t": 0.0},
        topic="lifecycle.stopped",
    )
    r = peek_terminal(open_run())
    assert isinstance(r, RunResult)
    assert r.outcome == "completed"
    assert r.reason == "completed"
    assert r.final_step == 500


def test_errored(open_run):
    open_run().send(
        {"completed": False, "error": "boom", "final_step": None, "t": 0.0},
        topic="lifecycle.stopped",
    )
    r = peek_terminal(open_run())
    assert r.outcome == "errored"
    assert r.reason == "errored"
    assert r.error == "boom"


def test_default_stop_is_preempted(open_run):
    # a clean stop with no completed claim -> preempted (the unmarked default)
    open_run().send(
        {"completed": False, "error": None, "final_step": 7, "t": 0.0},
        topic="lifecycle.stopped",
    )
    r = peek_terminal(open_run())
    assert r.outcome == "preempted"
    assert r.reason == "preempted"
    assert r.final_step == 7


def test_killed_from_launcher_terminated(open_run):
    # the worker died without a clean stop; the reaper recorded the manner
    open_run().send(
        {"reason": "killed", "signal": 9, "exit_code": None, "t": 0.0},
        topic="launcher.terminated",
        request_id="L1",
    )
    r = peek_terminal(open_run())
    assert r.outcome == "killed"
    assert r.reason == "killed"


def test_clean_stop_takes_precedence_over_terminated(open_run):
    ch = open_run()
    ch.send(
        {"completed": True, "error": None, "final_step": 9, "t": 0.0},
        topic="lifecycle.stopped",
    )
    ch.send(
        {"reason": "exited", "exit_code": 0, "signal": None, "t": 0.0},
        topic="launcher.terminated",
        request_id="L1",
    )
    assert peek_terminal(open_run()).outcome == "completed"


# The launcher tier is anchored to the CLAIMED episode and correlated by launch
# id (specs/launcher-record-identity.md). A third-party death record is neither
# self-identifying nor reliably ordered -- a reap is an observation that can land
# arbitrarily late -- so position cannot attribute it. These pin the two forgeries
# that position produced, both reproduced against the shipped launchers.


def _episode(ch, *, launch, pid, at):
    ch.send(
        {"handle": f"local://h/{pid}"}, topic="launcher.launched", request_id=launch
    )
    ch.send(
        {"handle": f"local://h/{pid}", "t": at},
        topic="lifecycle.started",
        request_id=launch,
    )


def test_a_late_reap_does_not_forge_the_live_episodes_verdict(open_run):
    # ep1 stops cleanly and lingers; ep2 launches, claims, and is LIVE; THEN
    # ep1's reap lands. Its death names ep1's launch -- it cannot speak for ep2.
    ch = open_run()
    _episode(ch, launch="L1", pid=1, at=0.0)
    ch.send(
        {"completed": True, "error": None, "final_step": 5, "t": 0.0},
        topic="lifecycle.stopped",
    )
    _episode(ch, launch="L2", pid=2, at=1.0)  #                      ep2 claims, live
    assert peek_terminal(open_run()) is None
    ch.send(
        {
            "reason": "exited",
            "exit_code": 0,
            "signal": None,
            "t": 0.0,
        },  # the LATE reap of ep1
        topic="launcher.terminated",
        request_id="L1",
    )
    assert peek_terminal(open_run()) is None  #                  ep2 still runs


def test_a_late_reap_is_attributed_to_its_own_episode_post_hoc(open_run):
    # both episodes dead, ep1's reap landing LAST: ep2's death is the verdict,
    # not the newest record. Attribution survives on a cold log, forever.
    ch = open_run()
    _episode(ch, launch="L1", pid=1, at=0.0)
    _episode(ch, launch="L2", pid=2, at=1.0)
    ch.send(
        {
            "reason": "killed",
            "exit_code": None,
            "signal": 9,
            "t": 0.0,
        },  # ep2 was killed
        topic="launcher.terminated",
        request_id="L2",
    )
    ch.send(
        {
            "reason": "exited",
            "exit_code": 0,
            "signal": None,
            "t": 0.0,
        },  # ep1's late, clean reap
        topic="launcher.terminated",
        request_id="L1",
    )
    assert peek_terminal(open_run()).outcome == "killed"  #      ep2's, not the newest


def test_a_claim_losers_clean_exit_does_not_complete_the_run(open_run):
    # the loser's launch is the NEWEST launched, and its clean exit the newest
    # terminated -- but no episode ever claimed it, so it speaks for nobody.
    ch = open_run()
    _episode(ch, launch="winner", pid=1, at=0.0)  #                  the winner claims
    ch.send({"handle": "local://h/2"}, topic="launcher.launched", request_id="loser")
    ch.send(
        {"reason": "exited", "exit_code": 0, "signal": None, "t": 0.0},
        topic="launcher.terminated",
        request_id="loser",
    )
    assert peek_terminal(open_run()) is None  #                  the winner runs on


def test_a_hand_run_workers_episode_has_no_launcher_verdict(open_run):
    # no launcher spawned this worker, so its claim names no launch and no
    # launcher record speaks for it -- an earlier launch's death least of all.
    ch = open_run()
    ch.send({"handle": "local://h/9"}, topic="launcher.launched", request_id="L1")
    ch.send(
        {"reason": "exited", "exit_code": 1, "signal": None, "t": 0.0},
        topic="launcher.terminated",
        request_id="L1",
    )
    assert (
        peek_terminal(open_run()).outcome == "errored"
    )  #     nobody claimed: L1 speaks
    ch.send(
        {"handle": local_handle(), "t": 1.0}, topic="lifecycle.started"
    )  #                            hand-run: no launch id
    assert peek_terminal(open_run()) is None  #                  ...and now L1 does not


def test_live_episode_running_then_none_when_stopped(open_run):
    ch = open_run()
    assert live_episode(open_run()) is None  #                     nothing yet
    ch.send({"handle": local_handle(), "t": 0.0}, topic="lifecycle.started")
    assert (
        live_episode(open_run()) == local_handle()
    )  #           running (our pid alive)
    ch.send(
        {"completed": True, "error": None, "final_step": 1, "t": 0.0},
        topic="lifecycle.stopped",
    )
    assert live_episode(open_run()) is None  #                     stopped -> not live


def test_peek_terminal_is_episode_aware(open_run):
    ch = open_run()
    # episode 1: started ... stopped
    ch.send({"handle": "local://h/1", "t": 0.0}, topic="lifecycle.started")
    ch.send(
        {"completed": True, "error": None, "final_step": 5, "t": 0.0},
        topic="lifecycle.stopped",
    )
    assert peek_terminal(open_run()).outcome == "completed"  #  ep1 terminal
    # episode 2 attaches -> the old stopped is no longer terminal (a started follows it)
    ch.send({"handle": "local://h/2", "t": 1.0}, topic="lifecycle.started")
    assert peek_terminal(open_run()) is None  #                 ep2 live
    # episode 2 stops -> terminal again, with ep2's verdict
    ch.send(
        {"completed": True, "error": None, "final_step": 9, "t": 0.0},
        topic="lifecycle.stopped",
    )
    assert peek_terminal(open_run()).final_step == 9


# ----- latest_episode: the episode-boundary rule, named once -----


def test_latest_episode_none_when_no_worker_ever_attached(open_run):
    assert latest_episode(open_run()) is None


def test_latest_episode_returns_the_started_envelope(open_run):
    # the raw envelope: .seq is the episode-window watermark
    # (read(after=e.seq, ...)), .body carries the handle. No Episode view type.
    seq = open_run().send(
        {"handle": "local://h/1", "t": 0.0},
        topic="lifecycle.started",
    )
    e = latest_episode(open_run())
    assert e.seq == seq
    assert e.body["handle"] == "local://h/1"


def test_latest_episode_survives_the_episodes_end(open_run):
    # *latest* means latest -- live, cleanly ended, or crashed alike (liveness
    # is live_episode's composition). A stopped run's latest episode is what a
    # status display shows: ended != absent.
    ch = open_run()
    seq = ch.send({"handle": "local://h/1", "t": 0.0}, topic="lifecycle.started")
    ch.send(
        {"completed": True, "error": None, "final_step": 5, "t": 0.0},
        topic="lifecycle.stopped",
    )
    assert latest_episode(open_run()).seq == seq


def test_latest_episode_tracks_the_newest_started(open_run):
    # started...stopped...started -> the second episode's opener. The rule
    # whose misapplication (oldest started) was audit F7's stale-pid bug.
    ch = open_run()
    ch.send({"handle": "local://h/1", "t": 0.0}, topic="lifecycle.started")
    ch.send(
        {"completed": False, "error": None, "final_step": 5, "t": 0.0},
        topic="lifecycle.stopped",
    )
    seq2 = ch.send({"handle": "local://h/2", "t": 1.0}, topic="lifecycle.started")
    e = latest_episode(open_run())
    assert e.seq == seq2
    assert e.body["handle"] == "local://h/2"


# ----- progress: the step frontier over the dense axis -----


def test_progress_none_when_no_stepped_record(open_run):
    # None for absence (the repo's convention), not an in-band -1 sentinel --
    # the memoizer keeps its private -1 adaptation locally.
    assert progress(open_run()) is None


def test_progress_from_heartbeat(open_run):
    open_run().send(
        {"step": 7, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat"
    )
    assert progress(open_run()) == 7


def test_progress_from_stopped_final_step(open_run):
    open_run().send(
        {"completed": False, "error": None, "final_step": 12, "t": 0.0},
        topic="lifecycle.stopped",
    )
    assert progress(open_run()) == 12


def test_progress_ignores_a_previous_episode_terminal(open_run):
    # A resumed run: episode 1 was preempted at step 5, episode 2 claimed and has
    # only reached step 3. `progress` must report the CURRENT episode's frontier --
    # reading episode 1's `final_step` makes `ensure` treat the window as closed and
    # return a series spliced across two episodes, as complete (runstate#33).
    ch = open_run()
    ch.send({"handle": "local://h/1", "t": 0.0}, topic="lifecycle.started")
    ch.send({"step": 5, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
    ch.send(
        {"completed": False, "error": None, "final_step": 5, "t": 0.0},
        topic="lifecycle.stopped",
    )
    ch.send({"handle": "local://h/2", "t": 1.0}, topic="lifecycle.started")
    ch.send({"step": 3, "consumed_seq": 0, "t": 1.0}, topic="lifecycle.heartbeat")
    assert progress(open_run()) == 3


def test_progress_is_the_max_of_both_axes(open_run):
    # frontier of the two registers: a prior episode's stopped may be ahead of
    # the live episode's heartbeat (extend resumed earlier) -- max wins.
    ch = open_run()
    ch.send(
        {"completed": False, "error": None, "final_step": 50, "t": 0.0},
        topic="lifecycle.stopped",
    )
    ch.send({"step": 30, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
    assert progress(open_run()) == 50


def test_progress_ignores_stepless_heartbeats(open_run):
    open_run().send(
        {"step": None, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat"
    )
    assert progress(open_run()) is None


# ----- value_series: the register projection on the (name, step) plane -----


def _value(ch, name, step, value, **env):
    ch.send({"value": value, "step": step, "t": 0.0}, topic="value", name=name, **env)


def test_value_series_groups_by_name_and_sorts_by_step(open_run):
    ch = open_run()
    _value(ch, "loss", 1, 0.9)
    _value(ch, "acc", 0, 0.1)
    _value(ch, "loss", 0, 1.0)  #  arrives after step 1's sample
    series = value_series(open_run())
    assert series == {"loss": {0: 1.0, 1: 0.9}, "acc": {0: 0.1}}
    assert list(series["loss"]) == [0, 1]  #  inner dicts step-sorted


def test_value_series_last_wins_per_cell(open_run):
    # two subscriptions to one name fire at the same step: duplicate samples
    # of one value, differing only in request_id -- the register projection
    # keeps the latest by seq (request_id is dedup-irrelevant).
    ch = open_run()
    _value(ch, "loss", 5, 0.5, request_id="a")
    _value(ch, "loss", 5, 0.4, request_id="b")
    assert value_series(open_run()) == {"loss": {5: 0.4}}


def test_value_series_rewind_resolves_to_the_resumed_branch(open_run):
    # ep1 reached step 6 then crashed before its checkpoint; ep2 resumed from
    # 5 and re-emitted. Last-wins-by-seq returns the as-resumed trajectory --
    # the orphaned branch drops out with zero episode-awareness code (the raw
    # events stay on the log for forensics).
    ch = open_run()
    _value(ch, "loss", 5, 0.50)
    _value(ch, "loss", 6, 0.45)  #         ep1's orphaned sample
    _value(ch, "loss", 5, 0.52)  #         ep2 re-emits step 5 (its branch)
    _value(ch, "loss", 6, 0.44)  #         ep2 reaches 6
    assert value_series(open_run())["loss"] == {5: 0.52, 6: 0.44}


def test_value_series_skips_records_outside_the_domain(open_run):
    # tolerant reader: a stepless emission is outside the step-indexed
    # observable's domain; a nameless value is not a series member; a foreign
    # body on the value topic (the substrate allows any dict) must not raise.
    ch = open_run()
    ch.send({"value": 1.0, "step": None, "t": 0.0}, topic="value", name="loss")
    ch.send({"value": 2.0, "step": 3, "t": 0.0}, topic="value")
    ch.send({"foo": "bar", "step": 3}, topic="value", name="loss")
    _value(ch, "loss", 4, 0.7)
    assert value_series(open_run()) == {"loss": {4: 0.7}}


def test_value_series_empty_channel(open_run):
    assert value_series(open_run()) == {}


# ----- live_demand: the positional answer fold's public home -----


def test_live_demand_empty(open_run):
    assert live_demand(open_run()) == []


def test_live_demand_subscribe_then_answers(open_run):
    ch = open_run()
    ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r1"
    )
    assert [e.request_id for e in live_demand(open_run())] == ["r1"]
    ch.send({}, topic="control.unsubscribe", request_id="r1")
    assert live_demand(open_run()) == []


def test_live_demand_nak_answers(open_run):
    ch = open_run()
    ch.send(
        {"from": {"step": 5}}, topic="control.subscribe", name="loss", request_id="r1"
    )
    ch.send(
        {"reason": "unsatisfiable", "message": "x"},
        topic="lifecycle.nak",
        request_id="r1",
    )
    assert live_demand(open_run()) == []


def test_live_demand_is_positional_not_an_id_set(open_run):
    ch = open_run()
    ch.send({}, topic="control.unsubscribe", request_id="r1")  #  answers nothing
    ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="a", request_id="r1"
    )
    ch.send({}, topic="control.unsubscribe", request_id="r1")  #  answers the above
    ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="b", request_id="r1"
    )
    live = live_demand(open_run())
    assert [e.name for e in live] == ["b"]  #     the later same-id subscribe is fresh


def test_live_demand_agrees_with_the_worker(open_run):
    from runstate.worker import Worker

    ch = open_run()
    ch.send(
        {"every": {"step": 1}, "until": {"time_seconds": 10}},
        topic="control.subscribe",
        name="loss",
        request_id="r1",
    )
    t = {"now": 0.0}
    w = Worker(open_run(), now=lambda: t["now"])
    w.set("loss", 1.0)
    w.tick(step=0)
    assert w.pinned is bool(live_demand(open_run()))
    t["now"] = 11.0
    w.tick(step=1)  #                              lease lapses; record written
    assert w.pinned is False
    assert live_demand(open_run()) == []  #    the fold sees the expiry record


def test_live_demand_excludes_boundary_voided_time_leases(open_run):
    # the observer form (specs/time-lease-boundary.md): a time-referencing
    # subscribe is live only while the latest episode is still its first
    # possible drainer.
    ch = open_run()
    ch.send(
        {"every": {"step": 1}, "until": {"time_seconds": 60}},
        topic="control.subscribe",
        name="loss",
        request_id="r1",
    )
    assert len(live_demand(open_run())) == 1  #    no boundary yet
    ch.send({"handle": "local://h/1", "t": 0.0}, topic="lifecycle.started")
    assert len(live_demand(open_run())) == 1  #    its first possible drainer
    ch.send({"handle": "local://h/2", "t": 1.0}, topic="lifecycle.started")
    assert live_demand(open_run()) == []  #        a boundary intervenes


def test_live_demand_keeps_step_keyed_subs_across_boundaries(open_run):
    ch = open_run()
    ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r1"
    )
    ch.send({"handle": "local://h/1", "t": 0.0}, topic="lifecycle.started")
    ch.send({"handle": "local://h/2", "t": 1.0}, topic="lifecycle.started")
    assert [e.request_id for e in live_demand(open_run())] == ["r1"]


# ----- the verdict/measurement split: verdict folds refuse to guess -----


def test_peek_terminal_typed_error_on_extra_key_stopped(open_run):
    # a verdict fold must not guess at an uninterpretable record: typed and
    # catchable, never the accidental bare TypeError of Stopped(**body).
    seq = open_run().send(
        {"completed": True, "error": None, "final_step": None, "oops": 1, "t": 0.0},
        topic="lifecycle.stopped",
    )
    with pytest.raises(MalformedRecordError) as ei:
        peek_terminal(open_run())
    assert ei.value.seq == seq
    assert ei.value.topic == "lifecycle.stopped"
    assert str(seq) in str(ei.value) and "lifecycle.stopped" in str(ei.value)


def test_peek_terminal_typed_error_on_missing_key_stopped(open_run):
    open_run().send({"completed": True}, topic="lifecycle.stopped")
    with pytest.raises(MalformedRecordError):
        peek_terminal(open_run())


def test_peek_terminal_typed_error_on_completed_with_error(open_run):
    # the payload constraint (completed => error is None) is a convention
    # violation like any other: ValueError from __post_init__ is wrapped too.
    open_run().send(
        {"completed": True, "error": "x", "final_step": None, "t": 0.0},
        topic="lifecycle.stopped",
    )
    with pytest.raises(MalformedRecordError):
        peek_terminal(open_run())


def test_peek_terminal_typed_error_on_malformed_terminated(open_run):
    seq = open_run().send(
        {"reason": "vanished", "exit_code": None, "signal": None, "t": 0.0},
        topic="launcher.terminated",
        request_id="L1",
    )
    with pytest.raises(MalformedRecordError) as ei:
        peek_terminal(open_run())
    assert ei.value.seq == seq
    assert ei.value.topic == "launcher.terminated"


def test_peek_terminal_typed_error_on_a_death_that_names_no_launch(open_run):
    # launcher-v0.3: a death record with no request_id is unattributable -- it
    # asserts the unknowable "the run is dead". The verdict plane refuses to
    # guess (it would forge), so it raises instead of quietly speaking.
    seq = open_run().send(
        {"reason": "exited", "exit_code": 0, "signal": None, "t": 0.0},
        topic="launcher.terminated",
    )
    with pytest.raises(MalformedRecordError) as ei:
        peek_terminal(open_run())
    assert ei.value.seq == seq
    assert "request_id" in ei.value.detail


def test_live_episode_typed_error_on_handleless_started(open_run):
    ch = open_run()
    ch.send({"t": 0.0}, topic="lifecycle.started")
    with pytest.raises(MalformedRecordError):
        live_episode(open_run())
    ch.send(
        {"handle": None, "t": 0.0}, topic="lifecycle.started"
    )  #  null handle: present but uninterpretable
    with pytest.raises(MalformedRecordError):
        live_episode(open_run())


def test_measurement_folds_skip_junk_records(open_run):
    # the other half of the split: progress / value_series / live_demand are
    # measurement folds -- one junk record is one lost point, skipped silently.
    ch = open_run()
    ch.send({"junk": True}, topic="lifecycle.heartbeat")
    ch.send({"junk": True}, topic="value", name="loss")
    ch.send(
        {"frm": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r1"
    )
    assert progress(open_run()) is None
    assert value_series(open_run()) == {}
    # live_demand is value-blind: a junk-bodied subscribe is still live demand
    assert [e.request_id for e in live_demand(open_run())] == ["r1"]


# ----- undischarged_stops: the stop-discharge fold's observer home -----


def test_undischarged_stops_pending_until_the_next_stopped(open_run):
    ch = open_run()
    assert undischarged_stops(open_run()) == []
    s1 = ch.send({}, topic="control.stop", request_id="a")
    s2 = ch.send({}, topic="control.stop")  #                    id-less: still a stop
    assert [e.seq for e in undischarged_stops(open_run())] == [s1, s2]
    ch.send(
        {"completed": False, "error": None, "final_step": 3, "t": 0.0},
        topic="lifecycle.stopped",
    )
    assert undischarged_stops(open_run()) == []  #           ONE stopped discharges ALL
    s3 = ch.send({}, topic="control.stop", request_id="b")
    assert [e.seq for e in undischarged_stops(open_run())] == [s3]


def test_undischarged_stops_pending_is_not_due(open_run):
    # a from-conditioned stop is pending the moment it lands, though the
    # worker won't honor it until the condition crosses: the fold reports
    # PENDING, deliberately never "due" (due needs the worker's coordinates).
    ch = open_run()
    ch.send({"from": {"step": 1000}}, topic="control.stop", request_id="late")
    assert [e.request_id for e in undischarged_stops(open_run())] == ["late"]


def test_undischarged_stops_overreports_naked_stops(open_run):
    # a malformed stop is refused by the worker (never in its pending set),
    # but no nak discharges a stop -- the fold conservatively lists it until
    # the next stopped discharges everything (never under-reports).
    from runstate.worker import Worker

    ch = open_run()
    ch.send({"bogus": 1}, topic="control.stop", request_id="bad")
    w = Worker(open_run(), now=lambda: 0.0)
    w.tick(step=0)  #                                            the worker naks it...
    assert ch.latest("lifecycle.nak") is not None
    assert [e.request_id for e in undischarged_stops(open_run())] == ["bad"]
    w.stopped()  #                                               ...the next stopped discharges
    assert undischarged_stops(open_run()) == []


def test_measurement_folds_skip_wrong_typed_junk(open_run):
    # type-junk is junk too: a wrong-typed step is not a measurement --
    # skipped, never leaked into the frontier or compared (no TypeError).
    ch = open_run()
    ch.send({"step": "abc", "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
    assert progress(open_run()) is None
    ch.send(
        {"completed": True, "error": None, "final_step": 3, "t": 0.0},
        topic="lifecycle.stopped",
    )
    assert progress(open_run()) == 3  #  the junk axis contributes nothing
    ch.send({"value": 1.0, "step": 0, "t": 0.0}, topic="value", name="loss")
    ch.send({"value": 9.9, "step": "x", "t": 1.0}, topic="value", name="loss")
    assert value_series(open_run()) == {"loss": {0: 1.0}}


def test_live_episode_crashed_local_episode_is_not_live(open_run):
    import socket

    ch = open_run()
    ch.send(
        {"handle": f"local://{socket.gethostname()}/2147483646", "t": 0.0},
        topic="lifecycle.started",
    )
    assert live_episode(open_run()) is None  #       dead pid, THIS host


def test_live_episode_foreign_host_episode_reads_live(open_run):
    # an unresolvable handle (another host) is conservatively LIVE -- the
    # waker never wakes a run it cannot probe (specs/lazy-launch.md).
    ch = open_run()
    ch.send(
        {"handle": "local://otherhost/2147483646", "t": 0.0}, topic="lifecycle.started"
    )
    assert live_episode(open_run()) == "local://otherhost/2147483646"


# --- the claim gate's precedence: a definitive probe outranks a record ----------------
#
# specs/launcher-record-identity.md pins the converse ("the live-guard must never be
# resolve()-based -- a probe voiding a *true* verdict is worse than the forgery"). What
# nothing asserted is the direction that admits a SECOND WRITER. Reproduced 2026-07-28:
# a patch routing live_episode through the launcher tier passed the whole suite
# unchanged while revoking a live worker's claim, and a standing driver loop then
# span 201 spawns in 3s (cf. specs/control-target.md R5's 373-in-3s).


def test_a_death_record_never_revokes_a_claim_whose_probe_says_alive(open_run):
    ch = open_run()
    live = local_handle()  #                    THIS process, so resolve() -> True
    ch.send({"handle": live}, topic="launcher.launched", request_id="L1")
    ch.send({"handle": live, "t": 0.0}, topic="lifecycle.started", request_id="L1")
    # A well-formed death, correctly correlated to the claim -- the shape a wrapper
    # (sbatch / srun / `nohup ... &`) produces when it exits while the worker it
    # spawned runs on, since the worker inherits RUNSTATE_LAUNCH_ID.
    ch.send(
        {"reason": "killed", "exit_code": None, "signal": 9, "t": 1.0},
        topic="launcher.terminated",
        request_id="L1",
    )
    assert peek_terminal(open_run()).outcome == "killed"  # the VERDICT plane may say so
    assert live_episode(open_run()) == live  #              the CLAIM plane must not


def test_a_malformed_stop_is_repairable_by_appending_a_good_one(open_run):
    # The stop tier parses only `latest`, so a later well-formed record supersedes a
    # poisoned one. Consumers depend on this: an append-only repair is the ONLY way to
    # revive a channel bricked by a bad write, and a downstream repair tool exists that
    # does exactly this. A fold that grew a full-history parse would silently take the
    # property away. (The launcher tier does not share it -- a known asymmetry.)
    ch = open_run()
    ch.send({"handle": "local://otherhost/1", "t": 0.0}, topic="lifecycle.started")
    ch.send(
        {"completed": False, "error": None, "final_step": 1, "t": 1.0, "note": "junk"},
        topic="lifecycle.stopped",
    )
    with pytest.raises(MalformedRecordError):
        peek_terminal(open_run())
    ch.send(
        {"completed": False, "error": None, "final_step": 1, "t": 2.0},
        topic="lifecycle.stopped",
    )
    assert peek_terminal(open_run()).outcome == "preempted"


def test_live_episode_ignores_a_launcher_death_that_peek_terminal_honours(open_run):
    # CHARACTERISATION, not an endorsement. The two folds have non-nested eliminator
    # sets: peek_terminal reads the launch-correlated `terminated`; live_episode reads
    # only `stopped` + resolve(). So a provably-dead run reads as still-claimed, and
    # relaunch_if_needed / ensure_served / the attach-CAS all refuse to act on it.
    #
    # Nothing in docs/ defends the omission and issue #17 asks for it to change. DELETE
    # this test when that is ruled -- it exists so the change is deliberate rather than
    # discovered, which twice cost a reviewer real effort.
    ch = open_run()
    dead = "local://otherhost/1"  #             resolve() abstains: not our pid table
    ch.send({"handle": dead}, topic="launcher.launched", request_id="L1")
    ch.send({"handle": dead, "t": 0.0}, topic="lifecycle.started", request_id="L1")
    ch.send(
        {"reason": "killed", "exit_code": None, "signal": 9, "t": 1.0},
        topic="launcher.terminated",
        request_id="L1",
    )
    assert peek_terminal(open_run()).outcome == "killed"
    assert live_episode(open_run()) == dead  #  the divergence, pinned


# --- the claim is the boundary: the launcher tier reads only the suffix -------------
#
# A death that speaks for THIS episode can only follow the claim it answers, so records
# at or before it are out of scope. Windowing the read is what makes the tier repairable
# (a poisoned record from a dead past cannot reach the live present) and what keeps its
# cost flat -- measured 3461 us -> 92 us at 1000 prior deaths.


def test_a_malformed_death_before_the_claim_does_not_poison_the_tier(open_run):
    # Unattributable death (no request_id), then a fresh claim. The bad record is a fact
    # about a past the current episode cannot have participated in, and an append-only
    # log can never retract it -- so letting it raise would brick the verdict plane for
    # this run forever. Contrast the stop tier, which is repairable by appending.
    ch = open_run()
    ch.send(
        {"reason": "exited", "exit_code": 0, "signal": None, "t": 0.0},
        topic="launcher.terminated",
    )
    ch.send({"handle": "local://h/2"}, topic="launcher.launched", request_id="L2")
    ch.send(
        {"handle": "local://h/2", "t": 1.0}, topic="lifecycle.started", request_id="L2"
    )
    assert peek_terminal(open_run()) is None  # not MalformedRecordError


def test_a_death_before_the_claim_cannot_speak_for_it_even_on_a_reused_id(open_run):
    # Correlation alone is not enough when an id repeats (a scheduler that reuses a job
    # id across a requeue, if a consumer derives launch ids from one). Position and
    # identity together: the death named THIS id, but it happened before THIS claim, so
    # it belongs to the previous episode. Without the window the run reads KILLED and
    # live at once -- a self-contradiction the boundary removes.
    ch = open_run()
    for pid, at in ((1, 0.0), (2, 2.0)):
        if pid == 2:  # the first episode's death lands before the second claims
            ch.send(
                {"reason": "killed", "exit_code": None, "signal": 9, "t": 1.0},
                topic="launcher.terminated",
                request_id="SHARED",
            )
        ch.send(
            {"handle": f"local://h/{pid}"},
            topic="launcher.launched",
            request_id="SHARED",
        )
        ch.send(
            {"handle": f"local://h/{pid}", "t": at},
            topic="lifecycle.started",
            request_id="SHARED",
        )
    assert peek_terminal(open_run()) is None  #      no verdict: the new episode runs
    assert live_episode(open_run()) == "local://h/2"  # and it holds the claim


def test_worker_completed_splits_the_two_completed_sources(open_run):
    # COMPLETED has two sources and only one speaks for the WORK. The worker's
    # own stopped(completed=True) does; a reaped launcher.terminated(exited, 0)
    # is a fact about a PROCESS -- an sbatch exits 0 at submit time.
    ch = open_run()
    ch.send(
        {"handle": "local://h/1", "t": 0.0}, topic="lifecycle.started", request_id="L1"
    )
    ch.send(
        {"handle": "local://h/1", "t": 0.0}, topic="launcher.launched", request_id="L1"
    )
    ch.send(
        {"reason": "exited", "exit_code": 0, "signal": None, "t": 1.0},
        topic="launcher.terminated",
        request_id="L1",
    )
    assert peek_terminal(open_run()).outcome == "completed"  # the launcher tier
    assert worker_completed(peek_terminal(open_run())) is False

    ch.send(
        {"completed": True, "error": None, "final_step": 2, "t": 2.0},
        topic="lifecycle.stopped",
    )
    assert worker_completed(peek_terminal(open_run())) is True
    assert worker_completed(None) is False
