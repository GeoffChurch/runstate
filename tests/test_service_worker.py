"""The service-worker spec (docs/specs/service-worker.md).

Expiry counter-records + the positional answer fold (a subscribe is live
until an unsubscribe/nak with its request_id FOLLOWS it by seq), the enforced
registered<=>fire-possible invariant, the count-atom hygiene, pinned/retire/
serve, and the death-CAS race discipline. Parametrized over both backends.
"""

import pytest

from runstate.worker import Worker


def _sub(ch, schedule, rid, name="loss"):
    return ch.send(schedule, topic="control.subscribe", name=name, request_id=rid)


# ----- expiry counter-records (piece 1) -----


def test_until_expiry_writes_the_counter_record(open_channel):
    orch = open_channel()
    _sub(orch, {"every": {"step": 1}, "until": {"time_seconds": 10}}, "r1")
    t = {"now": 0.0}
    w = Worker(open_channel(), now=lambda: t["now"])
    w.set("loss", 1.0)
    w.tick(step=0)  #                             registers + first fire
    t["now"] = 11.0
    w.tick(step=1)  #                             until met -> expire
    unsubs = open_channel().read(topics=["control.unsubscribe"])
    assert [u.request_id for u in unsubs] == ["r1"]


def test_one_shot_consumed_writes_the_counter_record(open_channel):
    orch = open_channel()
    _sub(orch, {}, "r1")  #                       fire-once-now
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 0.5)
    w.tick(step=0)  #                             fires and is consumed
    unsubs = open_channel().read(topics=["control.unsubscribe"])
    assert [u.request_id for u in unsubs] == ["r1"]
    # emit-then-delete lands the record before that tick's heartbeat beacon
    hb = open_channel().latest("lifecycle.heartbeat")
    assert unsubs[0].seq < hb.seq


def test_nak_is_the_answer_no_expiry_record_on_top(open_channel):
    # a refused subscribe is answered by its nak alone -- no unsubscribe record
    # on top (the expiry counter-record answers expired REGISTRATIONS, and the
    # structural gate refuses a bogus `every` before it can ever register).
    orch = open_channel()
    _sub(orch, {"every": {"bogus": 1}}, "bad")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=0)
    w.tick(step=1)
    assert open_channel().latest("lifecycle.nak").request_id == "bad"
    assert open_channel().read(topics=["value"]) == []  #  never registered, never fired
    assert open_channel().read(topics=["control.unsubscribe"]) == []


def test_worker_redrains_its_own_expiry_record_silently(open_channel):
    orch = open_channel()
    _sub(orch, {}, "r1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 0.5)
    w.tick(step=0)  #                             expiry record written
    w.tick(step=1)  #                             drains its own unsubscribe
    assert open_channel().read(topics=["lifecycle.nak"]) == []


# ----- the positional answer fold across episodes -----


def test_resumed_episode_does_not_resurrect_an_expired_lease(open_channel):
    # keyed by COUNT (not time) so this isolates the answer fold -- a time
    # lease would also be boundary-voided (specs/time-lease-boundary.md),
    # masking the expiry-record regression this test pins.
    orch = open_channel()
    _sub(orch, {"every": {"step": 1}, "until": {"count": 1}}, "r1")
    with Worker(open_channel(), now=lambda: 0.0) as w1:  #        episode 1
        w1.set("loss", 1.0)
        w1.tick(step=0)  #                        fires; count-until met; record
    n_values = len(open_channel().read(topics=["value"]))
    assert n_values == 1
    with Worker(open_channel(), now=lambda: 0.0) as w2:  #        episode 2
        w2.set("loss", 2.0)
        w2.tick(step=2)  #                        must NOT re-register r1
    assert len(open_channel().read(topics=["value"])) == n_values


def test_resumed_episode_skips_a_naked_subscribe(open_channel):
    # nak is final: no duplicate nak per episode; refused stays refused.
    orch = open_channel()
    _sub(orch, {"until": {"step": 50}}, "r1")  #  window already closed at 100
    with Worker(open_channel(), now=lambda: 0.0) as w1:
        w1.tick(step=100)  #                      nak: unsatisfiable
    assert len(open_channel().read(topics=["lifecycle.nak"])) == 1
    with Worker(open_channel(), now=lambda: 0.0) as w2:
        w2.tick(step=101)
    assert len(open_channel().read(topics=["lifecycle.nak"])) == 1


def test_same_id_resubscribe_after_answer_is_live(open_channel):
    # positional, not id-set: a later subscribe reusing an answered id is a
    # fresh, live request.
    orch = open_channel()
    _sub(orch, {"every": {"step": 1}}, "r1")
    orch.send({}, topic="control.unsubscribe", request_id="r1")  #  rescind
    _sub(orch, {"every": {"step": 1}}, "r1")  #                     again, later
    with Worker(open_channel(), now=lambda: 0.0) as w:
        w.set("loss", 1.0)
        w.tick(step=0)
    vals = open_channel().read(topics=["value"])
    assert [v.request_id for v in vals] == ["r1"]  #  served exactly once


def test_unsubscribe_before_its_subscribe_answers_nothing(open_channel):
    orch = open_channel()
    orch.send({}, topic="control.unsubscribe", request_id="r1")  #  too early
    _sub(orch, {"every": {"step": 1}}, "r1")
    with Worker(open_channel(), now=lambda: 0.0) as w:
        w.set("loss", 1.0)
        w.tick(step=0)
    assert len(open_channel().read(topics=["value"])) == 1


# ----- the enforced invariant: registered <=> a future fire is possible -----


def test_stepless_step_only_every_fires_once_then_expires(open_channel):
    # the most natural service subscription: {every: {step: 1}} on a stepless
    # worker -- fires its legitimate once, then expires with its record,
    # instead of pinning forever.
    orch = open_channel()
    _sub(orch, {"every": {"step": 1}}, "r1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=None)
    w.tick(step=None)
    assert len(open_channel().read(topics=["value"])) == 1
    assert [
        u.request_id for u in open_channel().read(topics=["control.unsubscribe"])
    ] == ["r1"]


def test_stepless_every_with_a_time_arm_recurs(open_channel):
    orch = open_channel()
    _sub(orch, {"every": {"any": [{"step": 1}, {"time_seconds": 5}]}}, "r1")
    t = {"now": 0.0}
    w = Worker(open_channel(), now=lambda: t["now"])
    w.set("loss", 1.0)
    w.tick(step=None)  #                          first fire
    t["now"] = 6.0
    w.tick(step=None)  #                          recurs on the time arm
    assert len(open_channel().read(topics=["value"])) == 2
    assert open_channel().read(topics=["control.unsubscribe"]) == []


# ----- count-atom hygiene (the accidental pure pin, closed) -----


@pytest.mark.parametrize(
    "schedule",
    [
        {"from": {"count": 1}},
        {"every": {"count": 2}},
        {"from": {"any": [{"count": 1}, {"step": 5}]}},
    ],
)
def test_count_atoms_outside_until_nak_malformed(open_channel, schedule):
    orch = open_channel()
    _sub(orch, schedule, "r1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=0)
    nak = open_channel().latest("lifecycle.nak")
    assert nak.request_id == "r1"
    assert nak.body["reason"] == "malformed"


def test_count_in_until_still_registers(open_channel):
    orch = open_channel()
    _sub(orch, {"every": {"step": 1}, "until": {"count": 2}}, "r1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=0)
    assert open_channel().latest("lifecycle.nak") is None
    assert len(open_channel().read(topics=["value"])) == 1


def test_count_in_stop_from_naks_malformed(open_channel):
    orch = open_channel()
    orch.send({"from": {"count": 1}}, topic="control.stop", request_id="s1")
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.tick(step=0) is False
    assert open_channel().latest("lifecycle.nak").body["reason"] == "malformed"


# ----- pinned / retire / serve (the careful death) -----


class _InjectOnFirstStopped:
    """Channel wrapper: just before the worker's first lifecycle.stopped send
    (the death-CAS), append a subscribe through a second handle -- the
    deterministic form of the subscribe-races-the-dying-breath interleaving."""

    def __init__(self, inner, orch):
        self._inner, self._orch, self._fired = inner, orch, False

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def send(self, body, **kw):
        if kw.get("topic") == "lifecycle.stopped" and not self._fired:
            self._fired = True
            self._orch.send(
                {"every": {"time_seconds": 1}},
                topic="control.subscribe",
                name="loss",
                request_id="raced",
            )
        return self._inner.send(body, **kw)


def test_pinned_states(open_channel):
    orch = open_channel()
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.pinned is False
    _sub(orch, {"every": {"time_seconds": 1}}, "r1")
    w.tick(step=None)
    assert w.pinned is True
    orch.send({}, topic="control.unsubscribe", request_id="r1")
    w.tick(step=None)
    assert w.pinned is False


def test_pinned_false_after_lease_lapse_within_the_tick(open_channel):
    orch = open_channel()
    _sub(orch, {"every": {"time_seconds": 1}, "until": {"time_seconds": 10}}, "r1")
    t = {"now": 0.0}
    w = Worker(open_channel(), now=lambda: t["now"])
    w.set("loss", 1.0)
    w.tick(step=None)
    assert w.pinned is True
    t["now"] = 11.0
    w.tick(step=None)  #                           lease lapses inside this tick
    assert w.pinned is False


def test_retire_wins_on_a_quiet_log(open_channel):
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=None)
    assert w.retire() is True
    e = open_channel().latest("lifecycle.stopped")
    assert e.body == {"completed": False, "error": None, "final_step": None, "t": 0.0}
    w.stopped()  #                                 __exit__ path: idempotent
    assert len(open_channel().read(topics=["lifecycle.stopped"])) == 1


def test_retire_returns_false_when_the_tail_holds_demand(open_channel):
    orch = open_channel()
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=None)
    _sub(orch, {"every": {"time_seconds": 1}}, "r1")  #  after the last tick
    assert w.retire() is False  #                  the fused read drains it
    assert w.pinned is True
    assert open_channel().latest("lifecycle.stopped") is None


def test_retire_loses_the_cas_to_a_raced_subscribe(open_channel):
    # the A1 interleaving: the subscribe lands BETWEEN retire's read and its
    # CAS -- the CAS must lose, the next read must register the subscriber.
    orch = open_channel()
    w = Worker(_InjectOnFirstStopped(open_channel(), orch), now=lambda: 0.0)
    w.tick(step=None)
    assert w.retire() is False
    assert w.pinned is True
    assert open_channel().latest("lifecycle.stopped") is None  # nobody died


def test_retire_own_append_then_wins(open_channel):
    # a malformed subscribe in the tail: retire's drain naks it (an own
    # append), which forces one more read; the CAS then wins cleanly.
    orch = open_channel()
    w = Worker(open_channel(), now=lambda: 0.0)
    w.tick(step=None)
    _sub(orch, {"from": {"count": 1}}, "bad")  #   will nak malformed
    assert w.retire() is True
    log = open_channel().read()
    assert log[-1].topic == "lifecycle.stopped"  # the stopped is last
    assert any(e.topic == "lifecycle.nak" and e.request_id == "bad" for e in log)


def test_serve_full_lifecycle(open_channel):
    # pre-staged lease -> serve from tick 0 -> lease lapses -> careful death.
    orch = open_channel()
    _sub(orch, {"every": {"time_seconds": 1}, "until": {"time_seconds": 10}}, "r1")
    t = {"now": 0.0}
    served = []
    with Worker(open_channel(), now=lambda: t["now"]) as w:
        for i in w.serve():
            w.set("cpu", float(i))
            served.append(i)
            t["now"] += 6.0  #                     two body cycles outlive the lease
    assert served and served[0] == 0  #            served from the first tick
    assert len(open_channel().read(topics=["value"])) >= 1
    stops = open_channel().read(topics=["lifecycle.stopped"])
    assert len(stops) == 1  #                      retire's breath; __exit__ no-ops
    assert [
        u.request_id for u in open_channel().read(topics=["control.unsubscribe"])
    ] == ["r1"]


def test_serve_exits_on_commanded_stop(open_channel):
    orch = open_channel()
    _sub(orch, {"every": {"time_seconds": 1}}, "r1")  #  open lease: stays pinned
    seen = []
    with Worker(open_channel(), now=lambda: 0.0) as w:
        for i in w.serve():
            w.set("cpu", 0.0)
            seen.append(i)
            if i == 1:
                orch.send({}, topic="control.stop", request_id="s1")
    assert seen == [0, 1]  #                       the stop drains at i==1's own tick
    assert len(open_channel().read(topics=["lifecycle.stopped"])) == 1


def test_serve_lost_claim_does_nothing(open_channel):
    from runstate.vocabulary.handle import local_handle

    ch = open_channel()
    ch.send(
        {"handle": local_handle(), "t": 0.0}, topic="lifecycle.started"
    )  #          a live episode already exists
    with Worker(open_channel(), now=lambda: 0.0) as w:
        assert list(w.serve()) == []
    assert open_channel().read(topics=["value", "lifecycle.stopped"]) == []


# ----- episode-scoped time-leases (specs/time-lease-boundary.md) -----


import socket

_DEAD = f"local://{socket.gethostname()}/2147483646"  #  dead pid, THIS host:
# resolve() must read it False (a fact), not None -- a foreign hostname would
# make every worker below LOSE its claim and these tests pass vacuously
# (specs/lazy-launch.md, the consistency sweep's finding).


def _dead_started(ch):
    return ch.send({"handle": _DEAD, "t": 0.0}, topic="lifecycle.started")


def test_founding_prestaged_time_lease_registers(open_channel):
    # the drainer's own started is not a boundary: the founding idiom lives.
    orch = open_channel()
    _sub(orch, {"every": {"step": 1}, "until": {"time_seconds": 100}}, "r1")
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=0)
    assert len(open_channel().read(topics=["value"])) == 1


def test_boundary_voids_a_time_lease(open_channel):
    # a started other than the drainer's own follows the lease -> voided:
    # pop-then-skip, no nak, no record -- the boundary started IS the
    # counter-record (pairing-by-seq's fourth instance).
    orch = open_channel()
    _sub(orch, {"every": {"step": 1}, "until": {"time_seconds": 100}}, "r1")
    _dead_started(orch)  #                         a prior episode's boundary
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.claimed is True  #  a lost worker emits nothing -- vacuous-green guard
    w.set("loss", 1.0)
    w.tick(step=0)
    assert open_channel().read(topics=["value"]) == []
    assert open_channel().read(topics=["lifecycle.nak"]) == []
    assert open_channel().read(topics=["control.unsubscribe"]) == []
    assert w.pinned is False


def test_voided_lease_pops_its_same_id_predecessor(open_channel):
    # the A1 attack: a client tightened an unbounded step-sub into a
    # time-leased replacement (same id); after a boundary the replacement is
    # voided -- and must still rescind the predecessor (slots, not sets),
    # else the superseded immortal sub resurrects while live_demand reads 0.
    orch = open_channel()
    _sub(orch, {"every": {"step": 1}}, "r1")  #                      immortal
    _sub(orch, {"every": {"step": 1}, "until": {"time_seconds": 100}}, "r1")
    _dead_started(orch)
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.claimed is True  #  a lost worker emits nothing -- vacuous-green guard
    w.set("loss", 1.0)
    w.tick(step=0)
    assert open_channel().read(topics=["value"]) == []
    assert w.pinned is False


def test_reanchor_once_then_void(open_channel):
    # a lease arriving DURING an episode is registered fresh by its first
    # possible drainer (the one permitted re-anchor), then voided by the
    # boundary after that.
    orch = open_channel()
    _dead_started(orch)  #                         "ep1", already dead
    _sub(orch, {"every": {"step": 1}, "until": {"time_seconds": 100}}, "r1")
    with Worker(open_channel(), now=lambda: 0.0) as w2:  #  first possible drainer
        w2.set("loss", 1.0)
        w2.tick(step=0)  #                         the one re-anchor: serves
    n = len(open_channel().read(topics=["value"]))
    assert n == 1
    with Worker(open_channel(), now=lambda: 0.0) as w3:  #  next boundary: voids
        w3.set("loss", 2.0)
        w3.tick(step=1)
    assert len(open_channel().read(topics=["value"])) == n


def test_zero_fire_void(open_channel):
    # consecutive crash-births around a pre-staged lease: voided, zero fires
    # ever (documented: acceptance != will-serve; renewal is the client's
    # detection mechanism).
    orch = open_channel()
    _sub(orch, {"every": {"step": 1}, "until": {"time_seconds": 100}}, "r1")
    _dead_started(orch)
    _dead_started(orch)
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=0)
    assert open_channel().read(topics=["value"]) == []
    assert w.pinned is False


def test_step_keyed_lease_crosses_boundaries(open_channel):
    # run-absolute schedules persist across episodes exactly as before.
    orch = open_channel()
    _sub(orch, {"every": {"step": 1}}, "r1")
    _dead_started(orch)
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=0)
    assert len(open_channel().read(topics=["value"])) == 1


def test_mixed_schedule_is_episode_scoped(open_channel):
    # any time atom anywhere makes the whole registration a lease.
    orch = open_channel()
    _sub(
        orch,
        {
            "every": {"step": 1},
            "until": {"any": [{"step": 1000}, {"time_seconds": 100}]},
        },
        "r1",
    )
    _dead_started(orch)
    w = Worker(open_channel(), now=lambda: 0.0)
    w.set("loss", 1.0)
    w.tick(step=0)
    assert open_channel().read(topics=["value"]) == []


def test_ghost_relaunch_bound(open_channel):
    # a dead lease with a boundary already after it costs exactly ONE
    # relaunch; the waker needs no flap policy.
    from runstate import live_demand

    orch = open_channel()
    _sub(orch, {"every": {"step": 1}, "until": {"time_seconds": 100}}, "ghost")
    _dead_started(orch)
    launches = 0
    while live_demand(open_channel()) and launches < 5:
        launches += 1
        with Worker(open_channel(), now=lambda: 0.0) as w:
            for _ in w.serve():
                w.set("cpu", 0.0)
    assert launches == 1
    assert live_demand(open_channel()) == []


# ----- lazy-launch (specs/lazy-launch.md) -----


def test_ensure_served_gates(open_channel, tmp_path):
    # demand + no live episode -> launch; no demand -> None; already live -> None.
    import sys
    from runstate import LocalLauncher, ensure_served, live_episode, peek_terminal

    root = tmp_path / "runs"
    root.mkdir()
    launcher = LocalLauncher(root=root)
    with launcher:
        # no demand at all -> no wake, even with no episode
        assert ensure_served(launcher, "svc", [sys.executable, "-c", "pass"]) is None
        # demand -> wake. The worker PACES its serve() loop: an unpaced loop
        # hammers heartbeat writes and, under DELETE's serialized writes, starves
        # other writers (the control.stop below). The long lease keeps the episode
        # live through the assertions (a short one can lapse mid-test under load,
        # and ensure_served is best-effort -- it would then race a doomed second
        # spawn); we end the worker explicitly with control.stop instead.
        ch = launcher.open_channel("svc")
        ch.send(
            {"every": {"time_seconds": 0.2}, "until": {"time_seconds": 30.0}},
            topic="control.subscribe",
            name="load",
            request_id="d1",
        )
        body = (
            "import runstate, time\n"
            "with runstate.Worker(runstate.attach()) as w:\n"
            "    for _ in w.serve():\n"
            "        w.set('load', 1.0)\n"
            "        time.sleep(0.05)\n"
        )
        h = ensure_served(launcher, "svc", [sys.executable, "-c", body])
        assert h is not None
        # already being served -> None: wait until the episode is actually LIVE
        # (not merely claimed) -- the condition ensure_served gates on.
        import time

        deadline = time.time() + 10
        while time.time() < deadline and live_episode(ch) is None:
            time.sleep(0.05)
        assert live_episode(ch) is not None
        assert ensure_served(launcher, "svc", [sys.executable, "-c", "pass"]) is None
        ch.send({}, topic="control.stop", request_id="stop1")  #  end it promptly
        h.wait(timeout=15)
    assert peek_terminal(launcher.open_channel("svc")) is not None


def test_stopped_is_lost_guarded(open_channel):
    # a claim-race loser may not act on the channel -- including an EXPLICIT
    # stopped() call (the minimal example's idiom would otherwise write a
    # completed claim onto the winner's live log).
    from runstate.vocabulary.handle import local_handle

    ch = open_channel()
    ch.send(
        {"handle": local_handle(), "t": 0.0}, topic="lifecycle.started"
    )  #          a live episode already exists
    w = Worker(open_channel(), now=lambda: 0.0)
    assert w.claimed is False
    w.stopped(completed=True)  #                   must be a silent no-op
    assert open_channel().read(topics=["lifecycle.stopped"]) == []


class _StubProc:
    def __init__(self, rc):
        self.returncode = rc

    def poll(self):
        return self.returncode


from runstate import peek_terminal
from runstate.vocabulary.handle import local_handle


def _local_handle_for(ch, handle, rc, launch_id):
    from runstate.launcher import _LocalHandle

    return _LocalHandle(
        run_id="r", channel=ch, handle=handle, _proc=_StubProc(rc), launch_id=launch_id
    )


# The reap is UNCONDITIONAL (specs/launcher-record-identity.md): a launcher
# reports what its own child did, and the correlation id says whose death it is.
# The old reap discipline's conditional silence -- suppressing a claim-loser's
# clean exit -- was a workaround for identity-less records; with identity, the
# writer stays honest and attribution is the reader's job (peek_terminal anchors
# to the claimed episode). These pin the honest corpse AND its powerlessness.


def test_the_claim_race_losers_corpse_lands_and_never_speaks_for_the_run(open_channel):
    ch = open_channel()
    ch.send(
        {"handle": f"local://{socket.gethostname()}/111"},
        topic="launcher.launched",
        request_id="loser",
    )
    ch.send(
        {"handle": f"local://{socket.gethostname()}/222"},
        topic="launcher.launched",
        request_id="winner",
    )
    ch.send(
        {"handle": f"local://{socket.gethostname()}/222", "t": 0.0},
        topic="lifecycle.started",
        request_id="winner",
    )  #    the winner claims
    h = _local_handle_for(
        ch, f"local://{socket.gethostname()}/111", rc=0, launch_id="loser"
    )
    h.poll()
    terms = open_channel().read(topics=["launcher.terminated"])
    assert len(terms) == 1 and terms[0].request_id == "loser"  #  the corpse IS recorded
    assert peek_terminal(open_channel()) is None  #               but the winner runs on


def test_the_null_workers_death_is_its_own_verdict(open_channel):
    # nobody claimed at all: terminated is the null worker's ONLY terminal.
    ch = open_channel()
    ch.send(
        {"handle": f"local://{socket.gethostname()}/111"},
        topic="launcher.launched",
        request_id="L1",
    )
    h = _local_handle_for(
        ch, f"local://{socket.gethostname()}/111", rc=3, launch_id="L1"
    )
    h.poll()
    terms = open_channel().read(topics=["launcher.terminated"])
    assert len(terms) == 1 and terms[0].body["exit_code"] == 3
    assert peek_terminal(open_channel()).outcome == "errored"


def test_an_unclean_death_beside_a_foreign_claim_stays_on_the_log(open_channel):
    # startup-crash visibility: the record lands (it is the only trace of that
    # launch), and the foreign live claim keeps it off the run's verdict.
    ch = open_channel()
    ch.send(
        {"handle": f"local://{socket.gethostname()}/111"},
        topic="launcher.launched",
        request_id="crasher",
    )
    ch.send(
        {"handle": local_handle(), "t": 0.0},
        topic="lifecycle.started",
        request_id="other",
    )
    h = _local_handle_for(
        ch, f"local://{socket.gethostname()}/111", rc=3, launch_id="crasher"
    )
    h.poll()
    terms = open_channel().read(topics=["launcher.terminated"])
    assert len(terms) == 1 and terms[0].body["exit_code"] == 3
    assert peek_terminal(open_channel()) is None


def test_double_waker_race_losers_corpse_does_not_forge_the_verdict(tmp_path):
    # two launches into one run: one claim wins. BOTH children leave a corpse
    # (the loser exits cleanly the moment it loses the CAS) -- and the verdict
    # is the winner's, because the loser's death names a launch nobody claimed.
    import sys
    from runstate import LocalLauncher, peek_terminal

    body = (
        "import runstate\n"
        "with runstate.Worker(runstate.attach()) as w:\n"
        "    for s in w.steps(total=3):\n"
        "        import time; time.sleep(0.05)\n"
    )
    root = tmp_path / "runs"
    root.mkdir()
    with LocalLauncher(root=root) as launcher:
        h1 = launcher.launch("race", [sys.executable, "-c", body])
        h2 = launcher.launch("race", [sys.executable, "-c", body])
        h1.wait(timeout=20)
        h2.wait(timeout=20)
    ch = launcher.open_channel("race")
    starteds = ch.read(topics=["lifecycle.started"])
    assert len(starteds) == 1  #                         exactly one claim
    claim = starteds[0].request_id
    terms = ch.read(topics=["launcher.terminated"])
    assert len(terms) == 2  #                            both children are reaped
    assert {t.request_id for t in terms} == {h1.launch_id, h2.launch_id}
    assert claim in (h1.launch_id, h2.launch_id)  #      the claim names its launch
    assert peek_terminal(ch).outcome == "preempted"  #   the winner's own verdict
