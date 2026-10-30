"""The claim plane and the answer fold, staged (commits-and-lineage.md §2, §5; plan
Task 7): ``ended``, the verdict by viewpoint, the gate, the run's freshness, the
claim's watermark, and demand and stops by name.

Over every backend (the ``ch`` fixture), since the reads use the Channel surface
alone. Ported from ``tests/test_observables.py`` and ``tests/test_run_episodes.py``
in 0.4.0 terms; the commit that added this file lists each old test's fate.
"""

import socket

import pytest

from runstate._next.observables import (
    LiveDemand,
    MalformedRecordError,
    Tier,
    current_heartbeat,
    last_activity,
    latest_episode,
    live_demand,
    live_episode,
    peek_terminal,
    series,
    undischarged_stops,
    worker_completed,
    LatestClaimHead,
)
from runstate._next.vocabulary.payloads import Outcome
from runstate.vocabulary.handle import local_handle

DEAD_HERE = f"local://{socket.gethostname()}/2147483646"  # this host, above pid_max


def claim(ch, handle="local://h/1", *, launch=None, resume_from=None, t=1.0, src=()):
    return ch.send(
        {"handle": handle, "t": t, "resume_from": resume_from, "sources": list(src)},
        topic="lifecycle.started",
        request_id=launch,
    )


def commit(ch, c, parent, values=(), *, fired=(), expired=(), consumed=0, t=2.0):
    return ch.send(
        {
            "claim_seq": c,
            "consumed_seq": consumed,
            "parent": parent,
            "values": list(values),
            "fired": list(fired),
            "expired": list(expired),
            "t": t,
        },
        topic="lifecycle.commit",
    )


def beat(ch, c, *, consumed=0, t=2.5):
    return ch.send(
        {"claim_seq": c, "consumed_seq": consumed, "t": t}, topic="lifecycle.heartbeat"
    )


def rewind(ch, c, resume_from, *, t=3.0):
    return ch.send(
        {"claim_seq": c, "resume_from": resume_from, "t": t}, topic="lifecycle.rewind"
    )


def exit_(ch, c, parent, values=(), *, completed=False, error=None, honored=(), t=4.0):
    return ch.send(
        {
            "completed": completed,
            "error": error,
            "claim_seq": c,
            "honored": list(honored),
            "parent": parent,
            "values": None if values is None else list(values),
            "fired": [],
            "expired": [],
            "t": t,
        },
        topic="lifecycle.stopped",
    )


def died(ch, c, outcome="killed", *, evidence="slurm:TIMEOUT", t=5.0, **junk):
    body = {"claim_seq": c, "outcome": outcome, "evidence": evidence, "t": t}
    return ch.send({**body, **junk}, topic="supervisor.died")


def launched(ch, launch, handle="local://h/1", *, t=0.5):
    return ch.send(
        {"handle": handle, "status": "running", "t": t},
        topic="supervisor.launched",
        request_id=launch,
    )


def terminated(ch, launch, *, code=None, signal=None, t=6.0, **junk):
    reason = "killed" if signal is not None else "exited"
    body = {"reason": reason, "exit_code": code, "signal": signal, "t": t}
    return ch.send({**body, **junk}, topic="supervisor.terminated", request_id=launch)


def value(ch, name, v, *, t=1.5):
    return ch.send({"value": v, "t": t}, topic="value", name=name)


def subscribe(ch, rid, source="loss", **schedule):
    return ch.send(schedule, topic="control.subscribe", name=source, request_id=rid)


def withdraw(ch, rid, *, t=4.5):
    return ch.send({"t": t}, topic="control.withdraw", request_id=rid)


def nak(ch, rid, reason="unsatisfiable"):
    return ch.send(
        {"reason": reason, "message": "no"}, topic="lifecycle.nak", request_id=rid
    )


def bound(ch, c, rid):
    return ch.send({"claim_seq": c}, topic="lifecycle.bound", request_id=rid)


def stop(ch, rid, **trigger):
    return ch.send(trigger, topic="control.stop", request_id=rid)


def demand(ch):
    return [e.request_id for e in live_demand(ch)]


def held(fold, ch):
    """A held ``LiveDemand``'s answer, polled now."""
    return [e.request_id for e in fold.poll(ch)]


def stops(ch):
    return [e.request_id for e in undischarged_stops(ch)]


# ----- ended, and the gate ----------------------------------------------------------


def test_live_episode_is_none_once_an_exit_or_a_died_names_the_latest_claim(ch):
    """The gate reads ``ended``; it never probes a handle: a dead same-host
    handle with no report reads live, since its probe is ``report_if_dead``'s."""
    assert live_episode(ch) is None  # nothing claimed
    c1 = claim(ch, DEAD_HERE)
    assert live_episode(ch) == DEAD_HERE  # never probed
    died(ch, c1, "lost", evidence="probe: pid gone")
    assert live_episode(ch) is None
    c2 = claim(ch, "local://h/2")
    died(ch, c1, "errored")  # names the older claim: ends nothing of c2's
    assert live_episode(ch) == "local://h/2"
    exit_(ch, c2, None)
    assert live_episode(ch) is None
    c3 = claim(ch, "local://h/3")
    died(ch, c3, evidence="")  # unreadable, but its claim_seq parses
    assert live_episode(ch) is None
    c4 = claim(ch, "local://h/4")
    died(ch, None, "killed")  # names no claim: ends nothing
    assert live_episode(ch) == "local://h/4"
    assert c4 > c3


def test_live_episode_foreign_host_episode_reads_live(ch):
    claim(ch, "local://otherhost/2147483646")
    assert live_episode(ch) == "local://otherhost/2147483646"


def test_live_episode_typed_error_on_handleless_started(ch):
    """An unreadable claim: the gate refuses to guess."""
    ch.send({"t": 0.0, "resume_from": None, "sources": []}, topic="lifecycle.started")
    with pytest.raises(MalformedRecordError):
        live_episode(ch)
    ch.send(
        {"handle": None, "t": 0.0, "resume_from": None, "sources": []},
        topic="lifecycle.started",
    )
    with pytest.raises(MalformedRecordError):
        live_episode(ch)


def test_a_supervisor_record_never_revokes_a_claim(ch):
    """A well-formed ``terminated`` correlated to the claim's launch, the shape a
    wrapper writes when it exits while the worker it spawned runs on, ends nothing:
    no fold matches a ``terminated`` to a claim (§2), so the gate and the verdict
    agree that the claim is held. Only a death report, from evidence, ends it."""
    live = local_handle()
    launched(ch, "L1", live)
    claim(ch, live, launch="L1")
    terminated(ch, "L1", signal=9, t=1.0)
    assert live_episode(ch) == live
    assert peek_terminal(ch) is None


# ----- latest_episode, unchanged ----------------------------------------------------


def test_latest_episode_none_when_no_worker_ever_attached(ch):
    assert latest_episode(ch) is None


def test_latest_episode_is_the_newest_claims_started_record(ch):
    seq = claim(ch, "local://h/1")
    e = latest_episode(ch)
    assert e.seq == seq and e.body["handle"] == "local://h/1"


def test_latest_episode_survives_the_episodes_end(ch):
    seq = claim(ch)
    exit_(ch, seq, None, completed=True)
    assert latest_episode(ch).seq == seq


def test_latest_episode_tracks_the_newest_started(ch):
    c1 = claim(ch, "local://h/1")
    exit_(ch, c1, None)
    seq2 = claim(ch, "local://h/2", t=2.0)
    e = latest_episode(ch)
    assert e.seq == seq2 and e.body["handle"] == "local://h/2"


# ----- the verdict ------------------------------------------------------------------


def test_the_verdict_is_none_while_the_claim_is_held(ch):
    c = claim(ch)
    n = commit(ch, c, None)
    beat(ch, c)
    assert peek_terminal(ch) is None
    rewind(ch, c, n)
    assert peek_terminal(ch) is None


def test_the_exit_outranks_a_terminated(ch):
    """A claimed run's verdict is its exit; its launch's ``terminated``, whatever
    it says, is not read."""
    c = claim(ch, launch="L1")
    exit_(ch, c, None, completed=True)
    terminated(ch, "L1", code=1)
    r = peek_terminal(ch)
    assert (r.tier, r.outcomes, r.node, r.claim_seq) == (
        Tier.EXIT,
        {Outcome.COMPLETED},
        c + 1,
        c,
    )


def test_a_late_reap_does_not_forge_the_live_episodes_verdict(ch):
    """Episode 1 exits; episode 2 claims and is live; then episode 1's reap lands.
    It ends nothing."""
    launched(ch, "L1")
    c1 = claim(ch, launch="L1")
    exit_(ch, c1, None, completed=True)
    launched(ch, "L2", "local://h/2")
    claim(ch, "local://h/2", launch="L2")
    assert peek_terminal(ch) is None
    terminated(ch, "L1", code=0)
    assert peek_terminal(ch) is None


def test_a_late_reap_is_not_the_verdict_the_death_report_naming_the_claim_is(ch):
    """Both episodes dead, episode 1's reap landing last: the verdict is episode
    2's death report, never the newest record, on a cold log, forever."""
    launched(ch, "L1")
    claim(ch, launch="L1")
    launched(ch, "L2", "local://h/2")
    c2 = claim(ch, "local://h/2", launch="L2")
    terminated(ch, "L2", signal=9)
    died(ch, c2, "killed", evidence="reap: signal 9")
    terminated(ch, "L1", code=0)
    r = peek_terminal(ch)
    assert (r.tier, r.outcomes, r.claim_seq) == (Tier.DIED, {Outcome.KILLED}, c2)


def test_a_claim_losers_clean_exit_does_not_complete_the_run(ch):
    """The loser's launch is the newest, and its exit 0 the newest ``terminated``,
    but nothing it launched claimed."""
    launched(ch, "winner")
    claim(ch, launch="winner")
    launched(ch, "loser", "local://h/2")
    terminated(ch, "loser", code=0)
    assert peek_terminal(ch) is None


def test_a_hand_run_workers_episode_has_no_launcher_verdict(ch):
    """Nothing claimed: the launch's ``terminated`` is the verdict. A hand-run
    claim, with no launch id, ends it as the run's verdict."""
    launched(ch, "L1", "local://h/9")
    terminated(ch, "L1", code=1)
    r = peek_terminal(ch)
    assert (r.tier, r.outcomes, r.claim_seq) == (
        Tier.TERMINATED,
        {Outcome.ERRORED},
        None,
    )
    claim(ch, local_handle())
    assert peek_terminal(ch) is None


def test_peek_terminal_is_episode_aware(ch):
    c1 = claim(ch, "local://h/1")
    exit_(ch, c1, None, completed=True)
    assert peek_terminal(ch).outcomes == {Outcome.COMPLETED}
    c2 = claim(ch, "local://h/2", t=2.0)
    assert peek_terminal(ch) is None
    n = exit_(ch, c2, None, completed=True)
    assert (peek_terminal(ch).node, peek_terminal(ch).claim_seq) == (n, c2)


def test_a_malformed_terminated_before_the_claim_does_not_poison_the_verdict(ch):
    """A ``terminated`` naming no launch, then a claim: the claimed run's verdict
    never reads it."""
    terminated(ch, None, code=0)
    launched(ch, "L2", "local://h/2")
    claim(ch, "local://h/2", launch="L2")
    assert peek_terminal(ch) is None


def test_a_death_before_the_claim_cannot_speak_for_it_even_on_a_reused_id(ch):
    """A launch id a scheduler reuses: the first episode's reap names it, but
    lands before the second claim, and no ``terminated`` speaks for a claim."""
    launched(ch, "SHARED", "local://h/1")
    claim(ch, "local://h/1", launch="SHARED")
    terminated(ch, "SHARED", signal=9, t=1.0)
    launched(ch, "SHARED", "local://h/2")
    claim(ch, "local://h/2", launch="SHARED", t=2.0)
    assert peek_terminal(ch) is None
    assert live_episode(ch) == "local://h/2"


def test_a_junk_typed_launcher_death_is_refused_too(ch):
    """JSON ``true`` is not an exit code: an unclaimed run's verdict rests on its
    ``terminated``, and refuses to guess."""
    launched(ch, "L1")
    ch.send(
        {"reason": "exited", "exit_code": True, "signal": None, "t": 0.0},
        topic="supervisor.terminated",
        request_id="L1",
    )
    with pytest.raises(MalformedRecordError, match="an exit carries a code"):
        peek_terminal(ch)


def test_worker_completed_is_a_closed_head(ch):
    """True for the claim's own completed exit only: a launch's exit 0 is a fact
    about a process, and a death report is never a completion."""
    assert worker_completed(None) is False
    launched(ch, "L1")
    terminated(ch, "L1", code=0)
    unclaimed = peek_terminal(ch)
    assert unclaimed.outcomes == {Outcome.COMPLETED}  # the launch's view
    assert worker_completed(unclaimed) is False
    c = claim(ch, launch="L1")
    n = commit(ch, c, None)
    died(ch, c, "preempted")
    assert worker_completed(peek_terminal(ch)) is False
    c2 = claim(ch, resume_from=n, t=3.0)
    exit_(ch, c2, n)
    assert worker_completed(peek_terminal(ch)) is False  # preempted: extendable
    c3 = claim(ch, resume_from=n, t=4.0)
    exit_(ch, c3, n, completed=True)
    assert worker_completed(peek_terminal(ch)) is True
    assert LatestClaimHead().resolve(ch).settled


def test_a_resumed_claim_extends_one_series(ch):
    """A relaunch with a higher target resumes from the previous claim's
    extendable exit and continues: one series along one lineage, no gaps or
    duplicates, and the run's verdict is the resumed claim's preempted exit."""
    parent = None
    c = claim(ch)
    for k in range(5):
        parent = commit(ch, c, parent, [value(ch, "loss", 5.0 - k)])
    parent = exit_(ch, c, parent)
    c = claim(ch, resume_from=parent, t=10.0)
    for k in range(5, 10):
        parent = commit(ch, c, parent, [value(ch, "loss", 5.0 - k)])
    exit_(ch, c, parent)
    losses = [e.value for e in series(ch, "loss", head=LatestClaimHead())]
    assert losses == [5.0 - k for k in range(10)]
    assert peek_terminal(ch).outcomes == {Outcome.PREEMPTED}


# ----- freshness and the watermark --------------------------------------------------


def test_last_activity_dates_the_workers_records_and_launched_never_died_or_terminated(
    ch,
):
    assert last_activity(ch) is None
    launched(ch, "L1", t=1.0)
    assert last_activity(ch) == 1.0  # a launched run has an age
    c = claim(ch, launch="L1", t=2.0)
    n = commit(ch, c, None, t=5.0)
    beat(ch, c, t=4.0)  # older than the commit: the max stands
    assert last_activity(ch) == 5.0
    rewind(ch, c, n, t=6.0)
    assert last_activity(ch) == 6.0
    exit_(ch, c, n, t=7.0)
    assert last_activity(ch) == 7.0
    died(ch, c, t=100.0)  # dated when its writer saw the end
    terminated(ch, "L1", code=0, t=200.0)
    assert last_activity(ch) == 7.0


def test_last_activity_never_reads_value_records(ch):
    """A value's ``t`` is the data plane's clock (present-nullable)."""
    value(ch, "loss", 1.0, t=999.0)
    assert last_activity(ch) is None


def test_last_activity_is_the_newest_dated_record(ch):
    c = claim(ch, t=10.0)
    assert last_activity(ch) == 10.0
    beat(ch, c, t=20.0)
    beat(ch, c, t=35.0)
    assert last_activity(ch) == 35.0
    exit_(ch, c, None, completed=True, t=40.0)
    assert last_activity(ch) == 40.0


def test_last_activity_skips_a_junk_t_measurement_fold(ch):
    c = claim(ch, t=10.0)
    ch.send(
        {"claim_seq": c, "consumed_seq": 0, "t": "junk"}, topic="lifecycle.heartbeat"
    )
    assert last_activity(ch) == 10.0


def test_current_heartbeat_is_the_claims_newest_commit_or_heartbeat(ch):
    """The records carrying the claim's ``consumed_seq``, whichever topic is
    newer, past a displaced worker's records written over it."""
    assert current_heartbeat(ch, None) is None
    a = claim(ch, "local://h/1")
    assert current_heartbeat(ch, latest_episode(ch)) is None
    a_commit = commit(ch, a, None, consumed=1)
    a_claim = latest_episode(ch)
    assert current_heartbeat(ch, a_claim).seq == a_commit
    a_beat = beat(ch, a, consumed=2)
    assert current_heartbeat(ch, a_claim).seq == a_beat
    b = claim(ch, "local://h/2", t=2.0)
    b_claim = latest_episode(ch)
    assert current_heartbeat(ch, b_claim) is None
    b_beat = beat(ch, b, consumed=b)
    a_late = commit(ch, a, a_commit, consumed=3)  # the displaced worker's
    for _ in range(100):  # bury b's beat under a's records
        beat(ch, a, consumed=3)
    assert current_heartbeat(ch, b_claim).seq == b_beat
    b_commit = commit(ch, b, None, consumed=b)
    assert current_heartbeat(ch, b_claim).seq == b_commit
    assert current_heartbeat(ch, a_claim).seq == b_commit - 1
    assert a_late < b_commit


# ----- the answer fold: demand ------------------------------------------------------


def test_live_demand_is_empty_on_an_empty_log(ch):
    assert live_demand(ch) == []


def test_a_subscription_is_spent_by_a_withdrawal_or_a_nodes_expired(ch):
    """A subscription is spent by a withdrawal, the asker's, or a node's
    ``expired``, the worker's."""
    subscribe(ch, "r1", every={"commits": 1})
    subscribe(ch, "r2", every={"commits": 1})
    assert demand(ch) == ["r1", "r2"]
    withdraw(ch, "r1")
    assert demand(ch) == ["r2"]
    c = claim(ch, src=["loss"])
    commit(ch, c, None, expired=["r2"])
    assert demand(ch) == []


def test_a_subscription_is_spent_by_a_nak(ch):
    subscribe(ch, "r1", **{"from": {"commits": 5}})
    nak(ch, "r1")
    assert live_demand(ch) == []


def test_live_demand_treats_a_spent_id_as_dead_by_name(ch):
    """A request_id names one request: once anything names it spent, wherever that
    sits, a later subscribe reusing it is dead on arrival. Replacement takes a
    fresh id."""
    withdraw(ch, "r1")  # spends r1 already
    subscribe(ch, "r1", "a", every={"commits": 1})
    withdraw(ch, "r1")
    subscribe(ch, "r1", "b", every={"commits": 1})
    assert live_demand(ch) == []
    subscribe(ch, "r2", "c", every={"commits": 1})
    assert [e.name for e in live_demand(ch)] == ["c"]


def test_live_demand_sees_a_lease_expired_at_a_node(ch):
    """A lease that lapses is named in the ``expired`` of the node where its
    ``until`` held; the fold reads it there."""
    subscribe(ch, "r1", every={"commits": 1}, until={"time_seconds": 10})
    c = claim(ch, src=["loss"])
    bound(ch, c, "r1")
    n = commit(ch, c, None, [value(ch, "loss", 1.0)], fired=["r1"])
    assert demand(ch) == ["r1"]
    commit(ch, c, n, expired=["r1"])
    assert live_demand(ch) == []


def test_a_lease_is_void_once_a_died_ends_its_claim(ch):
    """Void by its binding, by name: a lease nothing bound is not void, one bound
    to a held claim is live, and one bound to a claim a death report ended is
    void, for every later claim too. A durable subscription has no binding to
    void it."""
    subscribe(ch, "lease", every={"commits": 1}, until={"time_seconds": 60})
    subscribe(ch, "durable", every={"commits": 1})
    assert demand(ch) == ["lease", "durable"]
    c1 = claim(ch, src=["loss"])
    assert demand(ch) == ["lease", "durable"]  # nothing took it in yet
    c2 = claim(ch, "local://h/2", src=["loss"], t=2.0)
    bound(ch, c2, "lease")
    assert demand(ch) == ["lease", "durable"]  # bound to the held claim
    died(ch, c2, "lost", evidence="probe: pid gone")
    assert demand(ch) == ["durable"]  # its claim ended
    claim(ch, "local://h/3", src=["loss"], t=3.0)
    assert demand(ch) == ["durable"]  # still void: its claim ended
    assert c1 < c2


def test_live_demand_voids_a_time_lease_only_through_its_binding_by_name(ch):
    """A lease is void only through the claim it is bound to, ended by an exit as
    by a death report; a claim that never took it in voids nothing."""
    subscribe(ch, "r1", every={"commits": 1}, until={"time_seconds": 60})
    claim(ch, "local://h/1")
    c2 = claim(ch, "local://h/2", t=1.0)
    assert demand(ch) == ["r1"]
    bound(ch, c2, "r1")
    assert demand(ch) == ["r1"]
    exit_(ch, c2, None)
    assert live_demand(ch) == []
    claim(ch, "local://h/3", t=2.0)
    assert live_demand(ch) == []


def test_live_demand_keeps_count_keyed_subs_across_claims(ch):
    """A durable subscription lives per lineage, unbound: a new claim keeps it."""
    subscribe(ch, "r1", every={"commits": 1})
    claim(ch, "local://h/1")
    claim(ch, "local://h/2", t=1.0)
    assert demand(ch) == ["r1"]


def test_a_malformed_subscribe_is_live_until_its_nak(ch):
    """A body that does not parse as a schedule is live demand until a ``nak``
    names it, as a malformed stop is pending until its ``nak``: no fold judges a
    body the worker has not refused, and a waker must launch a worker to refuse
    it. A pure demand parses as one, so a ``from`` or an ``every`` makes it
    malformed. No claim binds one, so a claim's end never voids it."""
    fold = LiveDemand()
    subscribe(ch, "junk", frm={"commits": 1})
    subscribe(ch, "pure", None, **{"from": {"commits": 1}})
    subscribe(ch, "every", None, every={"commits": 1})
    subscribe(ch, "ok", None, until={"commits": 3})
    assert demand(ch) == ["junk", "pure", "every", "ok"] == held(fold, ch)
    c = claim(ch)
    bound(ch, c, "junk")  # a binding no worker writes for one: it still voids nothing
    died(ch, c, "killed")
    assert demand(ch) == ["junk", "pure", "every", "ok"] == held(fold, ch)
    for rid in ("junk", "pure", "every"):
        nak(ch, rid, reason="malformed")
    assert demand(ch) == ["ok"] == held(fold, ch)


def test_an_old_claims_contradictory_exits_do_not_poison_live_demand(ch):
    """``ended`` is the presence of an exit or a death report naming the claim,
    which never raises: claim A binds a lease and writes two readable exits, a
    malformed past; claim B posts a durable subscription. The lease is void and the
    durable one live, in the pure read and its fold alike. Two exits of the newest
    claim are the present's contradiction, and raise in both."""
    fold = LiveDemand()
    a = claim(ch)
    subscribe(ch, "lease", None, until={"time_seconds": 60})
    bound(ch, a, "lease")
    exit_(ch, a, None)
    exit_(ch, a, None)  # a second readable exit of A
    claim(ch, "local://h/2", t=2.0)
    subscribe(ch, "durable", None, until={"commits": 100})
    assert demand(ch) == ["durable"] == held(fold, ch)
    b = claim(ch, "local://h/3", t=3.0)
    exit_(ch, b, None)
    second = exit_(ch, b, None, completed=True)
    for read in (lambda: live_demand(ch), lambda: fold.poll(ch)):
        with pytest.raises(MalformedRecordError) as exc:
            read()
        assert exc.value.seq == second


def test_a_void_lease_resent_as_durable_stays_void(ch):
    """A subscription's class and target are its first send's (§3: a re-send that
    changes either is refused), so a lease voided by its claim's death does not
    come back as demand by being sent again as durable."""
    fold = LiveDemand()
    c = claim(ch, src=["lr"])
    subscribe(ch, "r", "lr", every={"commits": 1}, until={"time_seconds": 60})
    bound(ch, c, "r")
    died(ch, c, "killed")
    assert demand(ch) == [] == held(fold, ch)
    subscribe(ch, "r", "lr", every={"commits": 1})  # the class changed: durable
    assert demand(ch) == [] == held(fold, ch)


def test_live_demand_is_per_the_heads_lineage_and_empty_on_a_closed_head(ch):
    """A durable subscription's ``expired`` spends it on the head's lineage only
    (§2, "Per lineage"), so a rewind off the node that expired it revives it; and
    nothing is demanded of a closed head, until a new claim."""
    subscribe(ch, "once", every={"commits": 1}, until={"commits": 1})
    subscribe(ch, "live", every={"commits": 1})
    c = claim(ch, src=["loss"])
    commit(ch, c, None, [value(ch, "loss", 1.0)], fired=["live"], expired=["once"])
    assert demand(ch) == ["live"]
    rewind(ch, c, None)  # back to the root, where "once" never expired
    assert demand(ch) == ["once", "live"]
    exit_(ch, c, None, completed=True)
    assert live_demand(ch) == []  # closed
    subscribe(ch, "late", every={"commits": 1})
    assert live_demand(ch) == []  # never answered, never refused
    claim(ch, "local://h/2", src=["loss"], t=2.0)
    assert demand(ch) == ["once", "live", "late"]


def test_a_claim_that_resumes_below_an_expiry_revives_its_durable_demand(ch):
    """A answers "once" at its second node and exits; B resumes from A's first
    node, where "once" is unanswered, and dies before committing. Durable demand is
    per the head's lineage (§2, amended 2026-10-30), and the head is B's position,
    whose lineage has not expired "once": so the launch decision relaunches for
    it, as a running worker on that lineage would serve it, and the head's answers
    are not left truncated. A lease is the claim's, and stays spent."""
    subscribe(ch, "once", every={"commits": 1}, until={"commits": 2})
    subscribe(ch, "open", every={"commits": 1})
    subscribe(ch, "lease", every={"commits": 1}, until={"count": 1})
    a = claim(ch, src=["loss"])
    bound(ch, a, "lease")
    n1 = commit(
        ch,
        a,
        None,
        [value(ch, "loss", 1.0)],
        fired=["once", "open", "lease"],
        expired=["lease"],
    )
    n2 = commit(ch, a, n1, [value(ch, "loss", 0.9)], fired=["open"], expired=["once"])
    exit_(ch, a, n2)
    assert demand(ch) == ["open"]
    b = claim(ch, "local://h/2", resume_from=n1, src=["loss"], t=3.0)
    died(ch, b, "lost", evidence="reap: exit 0")
    assert demand(ch) == ["once", "open"]


def test_a_durable_subscription_another_lineage_expired_is_live_demand_on_the_head(
    ch,
):
    """§2 "Per lineage", §5 "The answer fold" (owner, 2026-10-30): a durable
    subscription is spent only by an ``expired`` on the head's lineage, so one
    another lineage expired is live demand again while the head stands on a
    lineage that has not; a lease is spent by any node's ``expired``, its claim's.
    The pure read and its held fold agree after every move: a rewind below the
    expiry, an expiry on the new branch, a rewind to the root, an exit, a claim
    resuming on the lineage that expired it, and one resuming below it."""
    fold = LiveDemand()

    def both():
        got = demand(ch)
        assert held(fold, ch) == got
        assert held(LiveDemand(), ch) == got  # a fresh fold, from its first poll
        return got

    subscribe(ch, "d", every={"commits": 1}, until={"commits": 2})
    subscribe(ch, "lease", every={"commits": 1}, until={"count": 2})
    c = claim(ch, src=["loss"])
    bound(ch, c, "lease")
    n1 = commit(ch, c, None, [value(ch, "loss", 1.0)], fired=["d", "lease"])
    assert both() == ["d", "lease"]
    lost = ["d", "lease"]
    n2 = commit(ch, c, n1, [value(ch, "loss", 0.9)], fired=lost, expired=lost)
    assert both() == []
    rewind(
        ch, c, n1
    )  # n1's lineage has not expired d; the lease's expiry is its claim's
    assert both() == ["d"]
    commit(ch, c, n1, [value(ch, "loss", 0.8)], fired=["d"], expired=["d"])
    assert both() == []
    rewind(ch, c, None)
    assert both() == ["d"]
    exit_(ch, c, None)  # the head is the exit, on the root: it expires nothing
    assert both() == ["d"]
    c2 = claim(ch, "local://h/2", resume_from=n2, src=["loss"], t=2.0)
    assert both() == []  # n2's lineage expired it
    died(ch, c2, "killed")
    claim(ch, "local://h/3", resume_from=n1, src=["loss"], t=3.0)
    assert both() == ["d"]


def test_a_withdrawn_subscription_lease_or_stop_is_spent(ch):
    """A withdrawal is the asker's, and spends the request it names, whatever its
    kind, before or after anything answered it; a re-send of the id stays spent."""
    subscribe(ch, "durable", every={"commits": 1})
    subscribe(ch, "lease", every={"time_seconds": 1})
    subscribe(ch, "pure", None, until={"time_seconds": 60})
    stop(ch, "halt")
    c = claim(ch, src=["loss"])
    bound(ch, c, "lease")
    assert demand(ch) == ["durable", "lease", "pure"]
    assert stops(ch) == ["halt"]
    for rid in ("durable", "lease", "pure", "halt"):
        withdraw(ch, rid)
    assert live_demand(ch) == [] and undischarged_stops(ch) == []
    subscribe(ch, "durable", every={"commits": 1})
    stop(ch, "halt")
    assert live_demand(ch) == [] and undischarged_stops(ch) == []


# ----- the answer fold: stops -------------------------------------------------------


def test_undischarged_stops_pending_until_the_next_stopped(ch):
    assert undischarged_stops(ch) == []
    s1 = stop(ch, "a")
    s2 = stop(ch, "b")
    assert [e.seq for e in undischarged_stops(ch)] == [s1, s2]
    c = claim(ch)
    exit_(ch, c, None, honored=["a", "b"])  # one exit names both
    assert undischarged_stops(ch) == []
    s3 = stop(ch, "c")
    assert [e.seq for e in undischarged_stops(ch)] == [s3]


def test_undischarged_stops_pending_is_not_due(ch):
    """Pending the moment it lands, though a worker honors it only once its
    ``from`` holds: the fold reports pending, never due."""
    stop(ch, "late", **{"from": {"commits": 1000}})
    assert stops(ch) == ["late"]


def test_undischarged_stops_drops_a_naked_stop_at_its_nak_by_name(ch):
    """A stop the worker refuses is answered by its nak."""
    stop(ch, "bad", bogus=1)
    assert stops(ch) == ["bad"]
    c = claim(ch)
    nak(ch, "bad", reason="malformed")
    assert undischarged_stops(ch) == []
    exit_(ch, c, None)
    assert undischarged_stops(ch) == []
