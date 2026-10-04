"""Reference by name (lifecycle-v0.5 / subscription-v0.3): a record that answers,
ends or concerns another names it by an id that is never reused, so relating
them is a join on that name and the answer does not depend on where records sit.

The scenarios that motivated it (runstate#39, the startless run, the stop that
lands after the last drain, the displacement cascade) plus the rules' edges."""

import itertools
import os
import socket

import pytest

import runstate.worker as worker_mod
from runstate.observables import (
    MalformedRecordError,
    latest_episode,
    live_demand,
    live_episode,
    peek_terminal,
    progress,
    undischarged_stops,
)
from runstate.worker import Worker

_HOST = socket.gethostname()
_pids = itertools.count(2147483000)
_ALIVE = f"local://{_HOST}/{os.getpid()}"


def _dead() -> str:
    return f"local://{_HOST}/{next(_pids)}"  #  > pid_max: never a live process


@pytest.fixture
def worker(monkeypatch):
    """Build a Worker whose liveness handle is chosen (dead by default), so a
    test can crash or displace an episode without killing a process."""

    def make(ch, *, handle=None, now=lambda: 0.0):
        h = handle or _dead()
        monkeypatch.setattr(worker_mod, "local_handle", lambda: h)
        w = Worker(ch, now=now)
        assert w.claimed
        return w

    return make


def _ids(envs):
    return [e.request_id for e in envs]


def _third_party_release(ch):
    claim = latest_episode(ch)
    ch.send(
        {
            "completed": False,
            "error": None,
            "final_step": None,
            "claim_seq": claim.seq if claim is not None else None,
            "honored": [],
            "t": 0.0,
        },
        topic="lifecycle.stopped",
    )


# ----- rule 1: stops are pending until a stopped NAMES them ---------------------


def test_a_third_party_release_discharges_no_stop(open_run, worker):
    # runstate#39: releasing a stranded claim used to answer every pending stop.
    ch = open_run()
    ep = worker(ch)
    ep.tick(step=0)  #                                       then it crashes
    ch.send({}, topic="control.stop", request_id="halt")
    _third_party_release(ch)
    assert live_episode(ch) is None  #   the release still releases (no auth)
    assert _ids(undischarged_stops(ch)) == ["halt"]  #  ...but answers nothing


def test_a_stop_sent_before_any_worker_is_honored_exactly_once(open_run, worker):
    ch = open_run()
    ch.send({}, topic="control.stop", request_id="pre")
    ep1 = worker(ch)
    assert ep1.tick(step=0) is True
    ep1.stopped()
    assert ch.latest("lifecycle.stopped").body["honored"] == ["pre"]
    assert undischarged_stops(ch) == []
    ep2 = worker(ch)
    assert ep2.tick(step=0) is False  #                       runs free


def test_a_claimless_terminal_halts_a_startless_run_by_naming_its_stop(open_run):
    # The startless run: a third party halts a run that never claimed. By name
    # it must NAME the stop it honors; claim_seq null = speaks for no episode.
    ch = open_run()
    ch.send({}, topic="control.stop", request_id="pre")
    ch.send(
        {
            "completed": False,
            "error": None,
            "final_step": None,
            "claim_seq": None,
            "honored": ["pre"],
            "t": 0.0,
        },
        topic="lifecycle.stopped",
    )
    assert undischarged_stops(ch) == []
    assert peek_terminal(ch).outcome == "preempted"


def test_a_stop_after_the_last_drain_is_named_by_the_dying_breath(open_run, worker):
    # Positionally it was "discharged unseen"; now the dying breath drains the
    # tail it CASes against, so the stop is seen, and named.
    ch = open_run()
    ep = worker(ch)
    ep.tick(step=0)  #                                       the last drain
    ch.send({}, topic="control.stop", request_id="late")
    ep.stopped(completed=True)
    assert ch.latest("lifecycle.stopped").body["honored"] == ["late"]
    assert undischarged_stops(ch) == []


class _RacingStop:
    """A channel view that slips a control.stop in just before the first
    dying-breath append, so that append's CAS sees a moved tail."""

    def __init__(self, ch):
        self._ch, self.fired = ch, False

    def send(self, body, *, topic, **kw):
        if topic == "lifecycle.stopped" and not self.fired:
            self.fired = True
            self._ch.send({}, topic="control.stop", request_id="race")
        return self._ch.send(body, topic=topic, **kw)

    def __getattr__(self, name):
        return getattr(self._ch, name)


def test_a_stop_racing_the_dying_breath_is_named(open_run, worker):
    ch = open_run()
    racing = _RacingStop(ch)
    ep = worker(racing)
    ep.tick(step=0)
    ep.stopped()
    assert racing.fired
    stopped = ch.read(topics=["lifecycle.stopped"])
    assert len(stopped) == 1  #               the lost CAS wrote nothing
    assert stopped[0].body["honored"] == ["race"]


def test_a_stop_after_the_dying_breath_blips_the_next_episode_once(open_run, worker):
    ch = open_run()
    ep1 = worker(ch)
    ep1.tick(step=0)
    ep1.stopped()
    ch.send({}, topic="control.stop", request_id="after")
    assert _ids(undischarged_stops(ch)) == ["after"]
    ep2 = worker(ch)
    assert ep2.tick(step=0) is True
    ep2.stopped()
    assert worker(ch).tick(step=0) is False


def test_a_nameless_stop_is_refused_and_never_pending(open_run, worker):
    ch = open_run()
    ch.send({}, topic="control.stop")  #                 subscription-v0.3: malformed
    assert undischarged_stops(ch) == []
    ep = worker(ch)
    assert ep.tick(step=0) is False
    naks = ch.read(topics=["lifecycle.nak"])
    assert [n.body["reason"] for n in naks] == ["malformed"]


def test_a_refused_stop_is_answered_by_its_nak(open_run, worker):
    # Positionally a naked stop over-reported until the next stopped; by name
    # the nak answers it.
    ch = open_run()
    ch.send({"from": {"bogus": 1}}, topic="control.stop", request_id="bad")
    assert _ids(undischarged_stops(ch)) == ["bad"]  # pending ≠ drained
    worker(ch).tick(step=0)
    assert undischarged_stops(ch) == []


# ----- rule 2: a request_id names ONE request -----------------------------------


def test_a_spent_request_id_is_dead_on_arrival(open_run, worker):
    ch = open_run()
    ch.send({}, topic="control.subscribe", name="loss", request_id="r")
    ch.send({}, topic="control.unsubscribe", request_id="r")
    ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r"
    )
    assert live_demand(ch) == []
    ep = worker(ch)
    ep.set("loss", 1.0)
    ep.tick(step=0)
    assert ch.read(topics=["value"]) == [] and not ep.pinned


def test_an_answer_counts_wherever_it_lands(open_run, worker):
    # an unsubscribe that precedes its subscribe still names it
    ch = open_run()
    ch.send({}, topic="control.unsubscribe", request_id="r")
    ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r"
    )
    assert live_demand(ch) == []
    ep = worker(ch)
    ep.set("loss", 1.0)
    ep.tick(step=0)
    assert ch.read(topics=["value"]) == []


def test_a_resend_of_a_live_id_is_the_same_request_updated(open_run, worker):
    ch = open_run()
    ch.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r"
    )
    s2 = ch.send(
        {"every": {"step": 1}, "until": {"step": 2}},
        topic="control.subscribe",
        name="loss",
        request_id="r",
    )
    assert [e.seq for e in live_demand(ch)] == [s2]  #  one request, latest schedule
    ep = worker(ch)
    for i in range(4):
        ep.set("loss", 1.0)
        ep.tick(step=i)
    assert len(ch.read(topics=["value"])) == 2  #       steps 0, 1; expired at 2


# ----- rule 3: a lease is bound to the episode that registered it ---------------

_LEASE = {"every": {"step": 1}, "until": {"time_seconds": 100}}


def test_the_registering_episode_binds_the_lease(open_run, worker):
    ch = open_run()
    ch.send(_LEASE, topic="control.subscribe", name="loss", request_id="L")
    ep1 = worker(ch)
    ep1.set("loss", 1.0)
    ep1.tick(step=0)
    bound = ch.read(topics=["lifecycle.bound"])
    assert [(b.request_id, b.body["claim_seq"]) for b in bound] == [
        ("L", latest_episode(ch).seq)
    ]
    assert _ids(live_demand(ch)) == ["L"]  #   its episode is live (no terminal)
    ep2 = worker(ch)  #                          ep1 crashed; a new episode claims
    ep2.set("loss", 1.0)
    ep2.tick(step=1)
    assert len(ch.read(topics=["value"])) == 1  #  ep2 does not re-anchor it
    assert live_demand(ch) == []


def test_a_lease_is_void_once_a_terminal_names_its_episode(open_run, worker):
    ch = open_run()
    ch.send(_LEASE, topic="control.subscribe", name="loss", request_id="L")
    ep1 = worker(ch)
    ep1.tick(step=0)
    ep1.stopped()
    assert live_demand(ch) == []  #  no wasted relaunch after a clean stop


def test_a_lease_no_episode_drained_survives_crash_births(open_run, worker):
    # positionally: voided with zero fires by two boundaries nobody drained at
    ch = open_run()
    ch.send(_LEASE, topic="control.subscribe", name="loss", request_id="L")
    worker(ch)  #                                 claims, dies before any drain
    worker(ch)
    ep3 = worker(ch)
    ep3.set("loss", 1.0)
    ep3.tick(step=0)
    assert len(ch.read(topics=["value"], request_ids=["L"])) == 1


# ----- rule 4: lifecycle records name their claim -------------------------------


def test_a_displaced_workers_terminal_does_not_release_its_successor(open_run, worker):
    ch = open_run()
    old = worker(ch)  #                                 dead handle: displaceable
    old.tick(step=0)
    new = worker(ch, handle=_ALIVE)
    new.tick(step=0)
    old.stopped()  #                         late, honest -- and names its own claim
    assert live_episode(ch) == _ALIVE
    assert peek_terminal(ch) is None


def test_a_displaced_workers_beats_do_not_move_progress(open_run, worker):
    ch = open_run()
    old = worker(ch)
    old.tick(step=0)
    new = worker(ch, handle=_ALIVE)
    new.tick(step=1)
    old.tick(step=999)
    assert progress(ch) == 1


def test_a_terminal_naming_no_claim_is_malformed_on_the_verdict_plane(open_run, worker):
    ch = open_run()
    worker(ch).tick(step=0)
    ch.send({"reason": "reclaimed"}, topic="lifecycle.stopped")
    with pytest.raises(MalformedRecordError):
        peek_terminal(ch)
    assert live_episode(ch) is None  #  dead handle; the junk record released nothing


def test_two_terminals_naming_one_claim_resolve_newest_first(open_run, worker):
    # The residual order: names cannot arbitrate a conflict between two reports
    # for the same episode, so the newest wins (exactly as it did positionally).
    ch = open_run()
    ep = worker(ch)
    ep.tick(step=0)
    ep.stopped(completed=True)
    _third_party_release(ch)
    assert peek_terminal(ch).outcome == "preempted"


# ----- order independence -------------------------------------------------------


def test_folds_ignore_where_answers_sit(open_run, worker):
    """The same facts in two orders that per-writer order allows: the worker's
    stopped (naming stop s1) before vs after a client's later stop s2 landing."""
    results = []
    for s2_first in (False, True):
        ch = open_run()
        ch.send({}, topic="control.stop", request_id="s1")
        ep = worker(ch)
        ep.tick(step=0)
        if s2_first:
            ch.send({}, topic="control.stop", request_id="s2")
            body = {
                "completed": False,
                "error": None,
                "final_step": 0,
                "claim_seq": latest_episode(ch).seq,
                "honored": ["s1"],
                "t": 0.0,
            }
            ch.send(body, topic="lifecycle.stopped")  #  as if s2 arrived unseen
        else:
            ep.stopped()
            ch.send({}, topic="control.stop", request_id="s2")
        results.append(_ids(undischarged_stops(ch)))
    assert results == [["s2"], ["s2"]]
