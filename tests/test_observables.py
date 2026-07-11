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
    latest_episode,
    live_demand,
    live_episode,
    peek_terminal,
    progress,
    undischarged_stops,
    value_series,
)
from runstate.vocabulary.handle import local_handle


def test_none_while_running(open_channel):
    ch = open_channel()
    ch.send({"step": 0, "consumed_seq": 0}, topic="lifecycle.heartbeat")
    assert peek_terminal(open_channel()) is None


def test_completed(open_channel):
    open_channel().send(
        {"completed": True, "error": None, "final_step": 500},
        topic="lifecycle.stopped",
    )
    r = peek_terminal(open_channel())
    assert isinstance(r, RunResult)
    assert r.outcome == "completed"
    assert r.reason == "completed"
    assert r.final_step == 500


def test_errored(open_channel):
    open_channel().send(
        {"completed": False, "error": "boom", "final_step": None},
        topic="lifecycle.stopped",
    )
    r = peek_terminal(open_channel())
    assert r.outcome == "errored"
    assert r.reason == "errored"
    assert r.error == "boom"


def test_default_stop_is_preempted(open_channel):
    # a clean stop with no completed claim -> preempted (the unmarked default)
    open_channel().send(
        {"completed": False, "error": None, "final_step": 7},
        topic="lifecycle.stopped",
    )
    r = peek_terminal(open_channel())
    assert r.outcome == "preempted"
    assert r.reason == "preempted"
    assert r.final_step == 7


def test_killed_from_launcher_terminated(open_channel):
    # the worker died without a clean stop; the reaper recorded the manner
    open_channel().send(
        {"reason": "killed", "signal": 9, "exit_code": None}, topic="launcher.terminated"
    )
    r = peek_terminal(open_channel())
    assert r.outcome == "killed"
    assert r.reason == "killed"


def test_clean_stop_takes_precedence_over_terminated(open_channel):
    ch = open_channel()
    ch.send({"completed": True, "error": None, "final_step": 9}, topic="lifecycle.stopped")
    ch.send({"reason": "exited", "exit_code": 0, "signal": None}, topic="launcher.terminated")
    assert peek_terminal(open_channel()).outcome == "completed"


def test_live_episode_running_then_none_when_stopped(open_channel):
    ch = open_channel()
    assert live_episode(open_channel()) is None                      # nothing yet
    ch.send({"handle": local_handle(), "attached_at": 0.0},
            topic="lifecycle.started")
    assert live_episode(open_channel()) == local_handle()            # running (our pid alive)
    ch.send({"completed": True, "error": None, "final_step": 1}, topic="lifecycle.stopped")
    assert live_episode(open_channel()) is None                      # stopped -> not live


def test_peek_terminal_is_episode_aware(open_channel):
    ch = open_channel()
    # episode 1: started ... stopped
    ch.send({"handle": "local://h/1", "attached_at": 0.0}, topic="lifecycle.started")
    ch.send({"completed": True, "error": None, "final_step": 5}, topic="lifecycle.stopped")
    assert peek_terminal(open_channel()).outcome == "completed"   # ep1 terminal
    # episode 2 attaches -> the old stopped is no longer terminal (a started follows it)
    ch.send({"handle": "local://h/2", "attached_at": 1.0}, topic="lifecycle.started")
    assert peek_terminal(open_channel()) is None                  # ep2 live
    # episode 2 stops -> terminal again, with ep2's verdict
    ch.send({"completed": True, "error": None, "final_step": 9}, topic="lifecycle.stopped")
    assert peek_terminal(open_channel()).final_step == 9


# ----- latest_episode: the episode-boundary rule, named once -----


def test_latest_episode_none_when_no_worker_ever_attached(open_channel):
    assert latest_episode(open_channel()) is None


def test_latest_episode_returns_the_started_envelope(open_channel):
    # the raw envelope: .seq is the episode-window watermark
    # (read(after=e.seq, ...)), .body carries the handle. No Episode view type.
    seq = open_channel().send(
        {"handle": "local://h/1", "attached_at": 0.0},
        topic="lifecycle.started",
    )
    e = latest_episode(open_channel())
    assert e.seq == seq
    assert e.body["handle"] == "local://h/1"


def test_latest_episode_survives_the_episodes_end(open_channel):
    # *latest* means latest -- live, cleanly ended, or crashed alike (liveness
    # is live_episode's composition). A stopped run's latest episode is what a
    # status display shows: ended != absent.
    ch = open_channel()
    seq = ch.send({"handle": "local://h/1", "attached_at": 0.0},
                  topic="lifecycle.started")
    ch.send({"completed": True, "error": None, "final_step": 5}, topic="lifecycle.stopped")
    assert latest_episode(open_channel()).seq == seq


def test_latest_episode_tracks_the_newest_started(open_channel):
    # started...stopped...started -> the second episode's opener. The rule
    # whose misapplication (oldest started) was audit F7's stale-pid bug.
    ch = open_channel()
    ch.send({"handle": "local://h/1", "attached_at": 0.0},
            topic="lifecycle.started")
    ch.send({"completed": False, "error": None, "final_step": 5}, topic="lifecycle.stopped")
    seq2 = ch.send({"handle": "local://h/2", "attached_at": 1.0},
                   topic="lifecycle.started")
    e = latest_episode(open_channel())
    assert e.seq == seq2
    assert e.body["handle"] == "local://h/2"


# ----- progress: the step frontier over the dense axis -----


def test_progress_none_when_no_stepped_record(open_channel):
    # None for absence (the repo's convention), not an in-band -1 sentinel --
    # the memoizer keeps its private -1 adaptation locally.
    assert progress(open_channel()) is None


def test_progress_from_heartbeat(open_channel):
    open_channel().send({"step": 7, "consumed_seq": 0}, topic="lifecycle.heartbeat")
    assert progress(open_channel()) == 7


def test_progress_from_stopped_final_step(open_channel):
    open_channel().send({"completed": False, "error": None, "final_step": 12},
                        topic="lifecycle.stopped")
    assert progress(open_channel()) == 12


def test_progress_is_the_max_of_both_axes(open_channel):
    # frontier of the two registers: a prior episode's stopped may be ahead of
    # the live episode's heartbeat (extend resumed earlier) -- max wins.
    ch = open_channel()
    ch.send({"completed": False, "error": None, "final_step": 50}, topic="lifecycle.stopped")
    ch.send({"step": 30, "consumed_seq": 0}, topic="lifecycle.heartbeat")
    assert progress(open_channel()) == 50


def test_progress_ignores_stepless_heartbeats(open_channel):
    open_channel().send({"step": None, "consumed_seq": 0}, topic="lifecycle.heartbeat")
    assert progress(open_channel()) is None


# ----- value_series: the register projection on the (name, step) plane -----


def _value(ch, name, step, value, **env):
    ch.send({"value": value, "step": step, "t": 0.0}, topic="value", name=name, **env)


def test_value_series_groups_by_name_and_sorts_by_step(open_channel):
    ch = open_channel()
    _value(ch, "loss", 1, 0.9)
    _value(ch, "acc", 0, 0.1)
    _value(ch, "loss", 0, 1.0)   # arrives after step 1's sample
    series = value_series(open_channel())
    assert series == {"loss": {0: 1.0, 1: 0.9}, "acc": {0: 0.1}}
    assert list(series["loss"]) == [0, 1]   # inner dicts step-sorted


def test_value_series_last_wins_per_cell(open_channel):
    # two subscriptions to one name fire at the same step: duplicate samples
    # of one value, differing only in request_id -- the register projection
    # keeps the latest by seq (request_id is dedup-irrelevant).
    ch = open_channel()
    _value(ch, "loss", 5, 0.5, request_id="a")
    _value(ch, "loss", 5, 0.4, request_id="b")
    assert value_series(open_channel()) == {"loss": {5: 0.4}}


def test_value_series_rewind_resolves_to_the_resumed_branch(open_channel):
    # ep1 reached step 6 then crashed before its checkpoint; ep2 resumed from
    # 5 and re-emitted. Last-wins-by-seq returns the as-resumed trajectory --
    # the orphaned branch drops out with zero episode-awareness code (the raw
    # events stay on the log for forensics).
    ch = open_channel()
    _value(ch, "loss", 5, 0.50)
    _value(ch, "loss", 6, 0.45)          # ep1's orphaned sample
    _value(ch, "loss", 5, 0.52)          # ep2 re-emits step 5 (its branch)
    _value(ch, "loss", 6, 0.44)          # ep2 reaches 6
    assert value_series(open_channel())["loss"] == {5: 0.52, 6: 0.44}


def test_value_series_skips_records_outside_the_domain(open_channel):
    # tolerant reader: a stepless emission is outside the step-indexed
    # observable's domain; a nameless value is not a series member; a foreign
    # body on the value topic (the substrate allows any dict) must not raise.
    ch = open_channel()
    ch.send({"value": 1.0, "step": None, "t": 0.0}, topic="value", name="loss")
    ch.send({"value": 2.0, "step": 3, "t": 0.0}, topic="value")
    ch.send({"foo": "bar", "step": 3}, topic="value", name="loss")
    _value(ch, "loss", 4, 0.7)
    assert value_series(open_channel()) == {"loss": {4: 0.7}}


def test_value_series_empty_channel(open_channel):
    assert value_series(open_channel()) == {}


# ----- live_demand: the positional answer fold's public home -----


def test_live_demand_empty(open_channel):
    assert live_demand(open_channel()) == []


def test_live_demand_subscribe_then_answers(open_channel):
    ch = open_channel()
    ch.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r1")
    assert [e.request_id for e in live_demand(open_channel())] == ["r1"]
    ch.send({}, topic="control.unsubscribe", request_id="r1")
    assert live_demand(open_channel()) == []


def test_live_demand_nak_answers(open_channel):
    ch = open_channel()
    ch.send({"from": {"step": 5}}, topic="control.subscribe", name="loss", request_id="r1")
    ch.send({"reason": "unsatisfiable", "message": "x"}, topic="lifecycle.nak",
            request_id="r1")
    assert live_demand(open_channel()) == []


def test_live_demand_is_positional_not_an_id_set(open_channel):
    ch = open_channel()
    ch.send({}, topic="control.unsubscribe", request_id="r1")   # answers nothing
    ch.send({"every": {"step": 1}}, topic="control.subscribe", name="a", request_id="r1")
    ch.send({}, topic="control.unsubscribe", request_id="r1")   # answers the above
    ch.send({"every": {"step": 1}}, topic="control.subscribe", name="b", request_id="r1")
    live = live_demand(open_channel())
    assert [e.name for e in live] == ["b"]      # the later same-id subscribe is fresh


def test_live_demand_agrees_with_the_worker(open_channel):
    from runstate.worker import Worker
    ch = open_channel()
    ch.send({"every": {"step": 1}, "until": {"time_seconds": 10}},
            topic="control.subscribe", name="loss", request_id="r1")
    t = {"now": 0.0}
    w = Worker(open_channel(), now=lambda: t["now"])
    w.set("loss", 1.0)
    w.tick(step=0)
    assert w.pinned is bool(live_demand(open_channel()))
    t["now"] = 11.0
    w.tick(step=1)                               # lease lapses; record written
    assert w.pinned is False
    assert live_demand(open_channel()) == []     # the fold sees the expiry record


def test_live_demand_excludes_boundary_voided_time_leases(open_channel):
    # the observer form (specs/time-lease-boundary.md): a time-referencing
    # subscribe is live only while the latest episode is still its first
    # possible drainer.
    ch = open_channel()
    ch.send({"every": {"step": 1}, "until": {"time_seconds": 60}},
            topic="control.subscribe", name="loss", request_id="r1")
    assert len(live_demand(open_channel())) == 1     # no boundary yet
    ch.send({"handle": "local://h/1", "attached_at": 0.0},
            topic="lifecycle.started")
    assert len(live_demand(open_channel())) == 1     # its first possible drainer
    ch.send({"handle": "local://h/2", "attached_at": 1.0},
            topic="lifecycle.started")
    assert live_demand(open_channel()) == []         # a boundary intervenes


def test_live_demand_keeps_step_keyed_subs_across_boundaries(open_channel):
    ch = open_channel()
    ch.send({"every": {"step": 1}}, topic="control.subscribe", name="loss",
            request_id="r1")
    ch.send({"handle": "local://h/1", "attached_at": 0.0},
            topic="lifecycle.started")
    ch.send({"handle": "local://h/2", "attached_at": 1.0},
            topic="lifecycle.started")
    assert [e.request_id for e in live_demand(open_channel())] == ["r1"]


# ----- the verdict/measurement split: verdict folds refuse to guess -----


def test_peek_terminal_typed_error_on_extra_key_stopped(open_channel):
    # a verdict fold must not guess at an uninterpretable record: typed and
    # catchable, never the accidental bare TypeError of Stopped(**body).
    seq = open_channel().send(
        {"completed": True, "error": None, "final_step": None, "oops": 1},
        topic="lifecycle.stopped",
    )
    with pytest.raises(MalformedRecordError) as ei:
        peek_terminal(open_channel())
    assert ei.value.seq == seq
    assert ei.value.topic == "lifecycle.stopped"
    assert str(seq) in str(ei.value) and "lifecycle.stopped" in str(ei.value)


def test_peek_terminal_typed_error_on_missing_key_stopped(open_channel):
    open_channel().send({"completed": True}, topic="lifecycle.stopped")
    with pytest.raises(MalformedRecordError):
        peek_terminal(open_channel())


def test_peek_terminal_typed_error_on_completed_with_error(open_channel):
    # the payload constraint (completed => error is None) is a convention
    # violation like any other: ValueError from __post_init__ is wrapped too.
    open_channel().send({"completed": True, "error": "x", "final_step": None},
                        topic="lifecycle.stopped")
    with pytest.raises(MalformedRecordError):
        peek_terminal(open_channel())


def test_peek_terminal_typed_error_on_malformed_terminated(open_channel):
    seq = open_channel().send(
        {"reason": "vanished", "exit_code": None, "signal": None},
        topic="launcher.terminated",
    )
    with pytest.raises(MalformedRecordError) as ei:
        peek_terminal(open_channel())
    assert ei.value.seq == seq
    assert ei.value.topic == "launcher.terminated"


def test_live_episode_typed_error_on_handleless_started(open_channel):
    ch = open_channel()
    ch.send({"attached_at": 0.0}, topic="lifecycle.started")
    with pytest.raises(MalformedRecordError):
        live_episode(open_channel())
    ch.send({"handle": None, "attached_at": 0.0},
            topic="lifecycle.started")   # null handle: present but uninterpretable
    with pytest.raises(MalformedRecordError):
        live_episode(open_channel())


def test_measurement_folds_skip_junk_records(open_channel):
    # the other half of the split: progress / value_series / live_demand are
    # measurement folds -- one junk record is one lost point, skipped silently.
    ch = open_channel()
    ch.send({"junk": True}, topic="lifecycle.heartbeat")
    ch.send({"junk": True}, topic="value", name="loss")
    ch.send({"frm": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r1")
    assert progress(open_channel()) is None
    assert value_series(open_channel()) == {}
    # live_demand is value-blind: a junk-bodied subscribe is still live demand
    assert [e.request_id for e in live_demand(open_channel())] == ["r1"]


# ----- undischarged_stops: the stop-discharge fold's observer home -----


def test_undischarged_stops_pending_until_the_next_stopped(open_channel):
    ch = open_channel()
    assert undischarged_stops(open_channel()) == []
    s1 = ch.send({}, topic="control.stop", request_id="a")
    s2 = ch.send({}, topic="control.stop")                     # id-less: still a stop
    assert [e.seq for e in undischarged_stops(open_channel())] == [s1, s2]
    ch.send({"completed": False, "error": None, "final_step": 3},
            topic="lifecycle.stopped")
    assert undischarged_stops(open_channel()) == []            # ONE stopped discharges ALL
    s3 = ch.send({}, topic="control.stop", request_id="b")
    assert [e.seq for e in undischarged_stops(open_channel())] == [s3]


def test_undischarged_stops_pending_is_not_due(open_channel):
    # a from-conditioned stop is pending the moment it lands, though the
    # worker won't honor it until the condition crosses: the fold reports
    # PENDING, deliberately never "due" (due needs the worker's coordinates).
    ch = open_channel()
    ch.send({"from": {"step": 1000}}, topic="control.stop", request_id="late")
    assert [e.request_id for e in undischarged_stops(open_channel())] == ["late"]


def test_undischarged_stops_overreports_naked_stops(open_channel):
    # a malformed stop is refused by the worker (never in its pending set),
    # but no nak discharges a stop -- the fold conservatively lists it until
    # the next stopped discharges everything (never under-reports).
    from runstate.worker import Worker

    ch = open_channel()
    ch.send({"bogus": 1}, topic="control.stop", request_id="bad")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=0)                                             # the worker naks it...
    assert ch.latest("lifecycle.nak") is not None
    assert [e.request_id for e in undischarged_stops(open_channel())] == ["bad"]
    w.stopped()                                                # ...the next stopped discharges
    assert undischarged_stops(open_channel()) == []


def test_measurement_folds_skip_wrong_typed_junk(open_channel):
    # type-junk is junk too: a wrong-typed step is not a measurement --
    # skipped, never leaked into the frontier or compared (no TypeError).
    ch = open_channel()
    ch.send({"step": "abc", "consumed_seq": 0}, topic="lifecycle.heartbeat")
    assert progress(open_channel()) is None
    ch.send({"completed": True, "error": None, "final_step": 3}, topic="lifecycle.stopped")
    assert progress(open_channel()) == 3   # the junk axis contributes nothing
    ch.send({"value": 1.0, "step": 0, "t": 0.0}, topic="value", name="loss")
    ch.send({"value": 9.9, "step": "x", "t": 1.0}, topic="value", name="loss")
    assert value_series(open_channel()) == {"loss": {0: 1.0}}


def test_live_episode_crashed_local_episode_is_not_live(open_channel):
    import socket
    ch = open_channel()
    ch.send({"handle": f"local://{socket.gethostname()}/2147483646",
             "attached_at": 0.0}, topic="lifecycle.started")
    assert live_episode(open_channel()) is None        # dead pid, THIS host


def test_live_episode_foreign_host_episode_reads_live(open_channel):
    # an unresolvable handle (another host) is conservatively LIVE -- the
    # waker never wakes a run it cannot probe (specs/lazy-launch.md).
    ch = open_channel()
    ch.send({"handle": "local://otherhost/2147483646",
             "attached_at": 0.0}, topic="lifecycle.started")
    assert live_episode(open_channel()) == "local://otherhost/2147483646"
