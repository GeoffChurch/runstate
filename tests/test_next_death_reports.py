"""Death reports, staged (docs/specs/commits-and-lineage.md §3, "Who writes a death
report", "The helpers", "The writers"; §5, the deciders; §10, "Death reports" and
``launcher.py``): ``report_death``, ``report_if_dead`` and ``ClaimAliveError``, the
launchers' ``supervisor.died``, and the deciders that report before they launch.

The claims here are hand-written 0.4.0 ``lifecycle.started`` records, as any
implementation's worker writes them, since the staged Worker comes later (plan,
Task 9). A ``LocalLauncher`` child writes its own: it opens its channel through the
locator and the environment its launcher set (``current_channel``,
``RUNSTATE_LAUNCH_ID``), as a worker of another language would. The tests
synchronize with a child by polling the log, or the child's handle, with a timeout.

``test_interop_death_report_from_evidence`` (§9 item 15) is in
``tests/test_next_interop.py``."""

import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import asdict

import pytest

from runstate._next.launcher import (
    ClaimAliveError,
    LocalLauncher,
    ThreadLauncher,
    ensure_served,
    relaunch_if_needed,
    report_death,
    report_if_dead,
)
from runstate._next.observables import (
    DeathReport,
    LiveDemand,
    Tier,
    live_episode,
    peek_terminal,
)
from runstate._next.vocabulary.payloads import Died, Outcome, Started, Topic
from runstate.channel.memory import MemoryChannel
from runstate.vocabulary.handle import local_handle
from runstate.vocabulary.launch import current_launch_id
from tests.test_next_reads import Rec, append, bound, channel, died, stopped, subscribe

K, E, L, P = Outcome.KILLED, Outcome.ERRORED, Outcome.LOST, Outcome.PREEMPTED

LIVE = local_handle()  # this process: probes alive
FOREIGN = "local://not-this-host/1"  # another host: the probe abstains


def dead_handle():
    """A handle on this host whose process has exited and been reaped: probes dead.
    (pid reuse could revive it, the limit §12 names; within a test it does not.)"""
    p = subprocess.Popen(["true"])
    p.wait()
    return f"local://{socket.gethostname()}/{p.pid}"


def claim(handle, *, launch=None, t=1.0):
    body = {"handle": handle, "t": t, "resume_from": None, "sources": []}
    return Rec("lifecycle.started", body, None, launch)


def until(fn, *, timeout=30.0):
    """Poll ``fn`` until it returns something other than ``None`` or ``False``."""
    deadline = time.monotonic() + timeout
    while (got := fn()) is None or got is False:
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting on {fn}")
        time.sleep(0.02)
    return got


def deaths(ch):
    """The ``supervisor.died`` bodies on the log, in seq order, as plain dicts."""
    return [
        {**e.body, "outcome": str(e.body["outcome"])}
        for e in ch.read(topics=[Topic.SUPERVISOR_DIED])
    ]


def died_body(claim_seq, outcome, evidence):
    """What a ``died`` the tests did not date says, less its ``t``."""
    return {"claim_seq": claim_seq, "outcome": str(outcome), "evidence": evidence}


def undated(bodies):
    return [{k: v for k, v in b.items() if k != "t"} for b in bodies]


# ----- report_death ----------------------------------------------------------------


def test_report_death_raises_on_a_live_probe_unless_forced():
    ch = channel([(1, claim(LIVE))])
    with pytest.raises(ClaimAliveError) as exc:
        report_death(ch, 1, outcome=K, evidence="slurm:CANCELLED", force=False)
    assert (exc.value.claim_seq, exc.value.handle) == (1, LIVE)
    assert ch.last_seq() == 1  # nothing written
    # forced: the caller's evidence outranks the probe
    assert report_death(ch, 1, outcome=K, evidence="slurm:CANCELLED", force=True) == 2
    assert undated(deaths(ch)) == [died_body(1, K, "slurm:CANCELLED")]
    # a dead probe, or one that cannot tell (another host), does not refuse
    for handle in (dead_handle(), FOREIGN):
        ch = channel([(1, claim(handle))])
        assert report_death(ch, 1, outcome=K, evidence="slurm:x", force=False) == 2


@pytest.mark.parametrize(
    "rows, claim_seq, outcome, evidence",
    [
        ([(1, claim(FOREIGN))], 1, Outcome.COMPLETED, "x"),  # a death is no completion
        ([(1, claim(FOREIGN))], 1, Outcome.PRESUMED_DEAD, "x"),  # nor a presumption
        ([(1, claim(FOREIGN))], 1, K, ""),  # evidence is non-empty
        ([(1, claim(FOREIGN))], 2, K, "x"),  # no record at the seq, at W
        ([(1, claim(FOREIGN)), (2, died(1, "killed"))], 2, K, "x"),  # not a claim
        ([(1, claim(FOREIGN))], 0, K, "x"),  # not a seq
    ],
)
def test_report_death_refuses_what_is_no_death_report(
    rows, claim_seq, outcome, evidence
):
    ch = channel(rows)
    with pytest.raises(ValueError):
        report_death(ch, claim_seq, outcome=outcome, evidence=evidence, force=True)
    assert ch.last_seq() == len(rows)


def test_report_death_returns_none_for_a_claim_with_a_readable_exit():
    for outcome in (K, E, L, P):
        ch = channel([(1, claim(FOREIGN)), (2, stopped(1, None, []))])
        assert report_death(ch, 1, outcome=outcome, evidence="x", force=True) is None
        assert ch.last_seq() == 2
    # an unreadable exit is no self-report: a report with a cause still lands
    ch = channel([(1, claim(FOREIGN)), (2, stopped(1, None, [], completed="yes"))])
    assert report_death(ch, 1, outcome=K, evidence="slurm:x", force=True) == 3


def test_a_lost_report_on_an_ended_claim_returns_none():
    ended_by = [
        died(1, "killed"),  # a death report
        Rec("supervisor.died", {"claim_seq": 1}, None, None),  # an unreadable one
        stopped(1, None, []),  # its own exit
    ]
    for end in ended_by:
        ch = channel([(1, claim(FOREIGN)), (2, end)])
        assert report_death(ch, 1, outcome=L, evidence="probe: x", force=True) is None
        assert ch.last_seq() == 2


def test_report_death_records_a_second_report():
    ch = channel([(1, claim(FOREIGN)), (2, died(1, "killed", evidence="signal 9"))])
    assert report_death(ch, 1, outcome=E, evidence="slurm:FAILED", force=True) == 3
    assert report_death(ch, 1, outcome=E, evidence="slurm:FAILED", force=True) == 4
    verdict = peek_terminal(ch)
    assert verdict.tier == Tier.DIED and verdict.outcomes == {K, E}
    assert [(r.seq, r.outcome, r.evidence) for r in verdict.reports] == [
        (2, K, "signal 9"),
        (3, E, "slurm:FAILED"),
        (4, E, "slurm:FAILED"),
    ]


class _Interloper(MemoryChannel):
    """A channel on which one record lands just before the first compare-and-append:
    the report's CAS at its W loses, and it must read again."""

    def __init__(self, rows, interloper):
        super().__init__()
        append(self, rows)
        self.interloper = interloper

    def send(self, body, *, topic, name=None, request_id=None, expected_seq=None):
        if expected_seq is not None and self.interloper is not None:
            r, self.interloper = self.interloper, None
            super().send(r.body, topic=r.topic, name=r.name, request_id=r.request_id)
        return super().send(
            body,
            topic=topic,
            name=name,
            request_id=request_id,
            expected_seq=expected_seq,
        )


def test_report_death_retries_a_lost_cas():
    beat = {"claim_seq": 1, "consumed_seq": 0, "t": 2.0}
    ch = _Interloper(
        [(1, claim(FOREIGN))], Rec("lifecycle.heartbeat", beat, None, None)
    )
    assert report_death(ch, 1, outcome=K, evidence="slurm:x", force=True) == 3
    assert [e.topic for e in ch.read()] == [
        Topic.LIFECYCLE_STARTED,
        Topic.LIFECYCLE_HEARTBEAT,
        Topic.SUPERVISOR_DIED,
    ]
    # the read again applies the rules at the new W: the claim's exit landed
    ch = _Interloper([(1, claim(FOREIGN))], stopped(1, None, []))
    assert report_death(ch, 1, outcome=K, evidence="slurm:x", force=True) is None
    assert ch.last_seq() == 2


# ----- report_if_dead --------------------------------------------------------------


def test_report_if_dead_reports_a_held_claims_same_host_dead_handle_as_lost():
    handle = dead_handle()
    ch = channel([(1, claim(handle))])
    assert report_if_dead(ch) == 2
    assert undated(deaths(ch)) == [died_body(1, L, f"probe: {handle}")]
    assert peek_terminal(ch).outcomes == {L}
    # evidence, never inference: a live handle, and one on another host, report
    # nothing, however old the claim
    for handle in (LIVE, FOREIGN):
        ch = channel([(1, claim(handle, t=0.0))])
        assert report_if_dead(ch) is None
        assert ch.last_seq() == 1


def test_report_if_dead_returns_none_when_the_pid_is_reused_between_probe_and_report(
    monkeypatch,
):
    """Its probe finds the pid dead, and the report's own probe finds it alive: a
    new process took the pid in between. The report refuses, and so it reports
    nothing (plan, Task 9)."""
    from runstate._next import launcher

    answers = iter([False, True])
    monkeypatch.setattr(launcher, "resolve", lambda handle: next(answers))
    ch = channel([(1, claim("local://somewhere/42"))])
    assert report_if_dead(ch) is None
    assert ch.last_seq() == 1


def test_report_if_dead_reports_nothing_once_the_claim_has_ended():
    handle = dead_handle()
    assert report_if_dead(MemoryChannel()) is None  # nothing claimed
    for end in (stopped(1, None, []), died(1, "killed")):
        ch = channel([(1, claim(handle)), (2, end)])
        assert report_if_dead(ch) is None
        assert ch.last_seq() == 2
    # an older claim's dead handle is not the newest claim's
    ch = channel([(1, claim(handle)), (2, died(1, "killed")), (3, claim(LIVE))])
    assert report_if_dead(ch) is None
    assert ch.last_seq() == 3


def test_report_if_dead_reports_a_held_claim_whose_session_ended_though_its_pid_lives(
    pg_ready,
):
    """The session probe outranks a live pid on the same host: a claim written
    through a channel then closed, in this live process (§3, "A session probe")."""
    from runstate.channel.postgres import PostgresChannel

    run_id = f"session-{uuid.uuid4()}"
    writer = PostgresChannel(pg_ready, run_id=run_id)
    probe = PostgresChannel(pg_ready, run_id=run_id)
    try:
        body = asdict(Started(handle=LIVE, t=1.0, resume_from=None, sources=[]))
        seq = writer.send(body, topic=Topic.LIFECYCLE_STARTED, expected_seq=0)
        session = probe.writer_session(seq)
        assert report_if_dead(probe) is None  # its session and its pid both live
        writer.close()
        report = until(lambda: report_if_dead(probe), timeout=10.0)
        assert undated(deaths(probe)) == [
            died_body(seq, L, f"session: {session.server}/{session.key}")
        ]
        assert [e.seq for e in probe.read(topics=[Topic.SUPERVISOR_DIED])] == [report]
        assert peek_terminal(probe).outcomes == {L}
    finally:
        writer.close()
        probe.close()


# ----- the launchers ---------------------------------------------------------------

# A worker of any implementation: it opens its channel through the locator and the
# environment its launcher set, CAS-appends a 0.4.0 claim naming its launch, then
# does what its arguments say, in order:
#   release      a death report naming its claim lands (the claim has ended)
#   exit N       it exits with code N
#   signal N     it kills itself with signal N
#   block        it sleeps until something kills it
CHILD = """
import os, sys, time
from runstate import current_channel
from runstate.vocabulary.handle import local_handle
from runstate.vocabulary.launch import current_launch_id

ch = current_channel()
body = {"handle": local_handle(), "t": time.time(), "resume_from": None, "sources": []}
claim = None
while claim is None:
    claim = ch.send(
        body,
        topic="lifecycle.started",
        request_id=current_launch_id(),
        expected_seq=ch.last_seq(),
    )
args = sys.argv[1:]
while args:
    act, args = args[0], args[1:]
    if act == "release":
        ch.send(
            {"claim_seq": claim, "outcome": "killed", "evidence": "test: released",
             "t": time.time()},
            topic="supervisor.died",
        )
    elif act == "exit":
        ch.close()
        sys.exit(int(args.pop(0)))
    elif act == "signal":
        ch.close()
        os.kill(os.getpid(), int(args.pop(0)))
        time.sleep(60)
    elif act == "block":
        time.sleep(120)
"""


def child(*acts):
    return [sys.executable, "-c", CHILD, *acts]


def launched_claim(ch):
    """The claim the child wrote, polled for."""
    return until(lambda: ch.latest(Topic.LIFECYCLE_STARTED))


def terminated(ch):
    return [
        (e.request_id, e.body["reason"], e.body["exit_code"], e.body["signal"])
        for e in ch.read(topics=[Topic.SUPERVISOR_TERMINATED])
    ]


@pytest.mark.parametrize("reaper", ["wait", "poll"])
def test_local_launchers_reap_writes_died_when_the_handles_match_whoever_waited(
    tmp_path, reaper
):
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("run", child("exit", "3"))
        if reaper == "wait":
            assert h.wait(timeout=30) == 3
        else:
            assert until(lambda: h.poll()) == 3
        ch = launcher.attach_channel("run")
        claim_seq = launched_claim(ch).seq
        assert ch.latest(Topic.LIFECYCLE_STARTED).request_id == h.launch_id
        assert terminated(ch) == [(h.launch_id, "exited", 3, None)]
        assert undated(deaths(ch)) == [died_body(claim_seq, E, "reap: exit 3")]
        h.wait()  # a second reap writes nothing more
    assert len(deaths(ch)) == 1 and len(terminated(ch)) == 1
    ch.close()


def test_local_launchers_reap_writes_only_terminated_when_the_handles_differ(
    tmp_path,
):
    """A wrapper whose grandchild claims: the launch's process is not the claimant,
    so its exit says nothing of the claim (§3, "The writers")."""
    script = tmp_path / "worker.py"
    script.write_text(CHILD)
    wrapper = ["sh", "-c", f'"{sys.executable}" "{script}" exit 0; exit 3']
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("run", wrapper)
        assert h.wait(timeout=30) == 3
        ch = launcher.attach_channel("run")
        started = launched_claim(ch)
        assert started.request_id == h.launch_id  # it names the launch, but
        assert started.body["handle"] != h.handle  # it is not the launch's process
        assert terminated(ch) == [(h.launch_id, "exited", 3, None)]
        assert deaths(ch) == []
        assert peek_terminal(ch) is None  # the claim is held
    ch.close()


def test_thread_launcher_writes_died_when_its_thread_finishes_with_no_exit(tmp_path):
    def claims(ch, *, exits):
        body = asdict(Started(handle=LIVE, t=1.0, resume_from=None, sources=[]))
        seq = ch.send(
            body,
            topic=Topic.LIFECYCLE_STARTED,
            request_id=current_launch_id(),
            expected_seq=ch.last_seq(),
        )
        if exits:
            ch.send(
                {
                    "completed": True,
                    "error": None,
                    "claim_seq": seq,
                    "honored": [],
                    "parent": None,
                    "values": [],
                    "fired": [],
                    "expired": [],
                    "t": 2.0,
                },
                topic=Topic.LIFECYCLE_STOPPED,
                expected_seq=ch.last_seq(),
            )

    launcher = ThreadLauncher(root=tmp_path)
    h = launcher.launch("no-exit", claims, kwargs={"exits": False})
    h.wait()
    ch = launcher.attach_channel("no-exit")
    assert [e.topic for e in ch.read()] == [
        Topic.SUPERVISOR_LAUNCHED,
        Topic.LIFECYCLE_STARTED,
        Topic.SUPERVISOR_TERMINATED,
        Topic.SUPERVISOR_DIED,
    ]
    assert ch.latest(Topic.SUPERVISOR_LAUNCHED).request_id == h.launch_id
    assert undated(deaths(ch)) == [died_body(2, L, "reap: returned")]
    # with its own exit, the thread's end adds nothing
    h = launcher.launch("exited", claims, kwargs={"exits": True})
    h.wait()
    ch = launcher.attach_channel("exited")
    assert deaths(ch) == [] and terminated(ch) == [(h.launch_id, "exited", 0, None)]
    # and a launch nothing claimed writes its terminated only, its run's verdict
    h = launcher.launch("unclaimed", lambda ch: None)
    h.wait()
    ch = launcher.attach_channel("unclaimed")
    assert deaths(ch) == [] and peek_terminal(ch).tier == Tier.TERMINATED


@pytest.mark.parametrize("released", [False, True], ids=["held", "ended"])
def test_a_signal_reports_killed_and_a_nonzero_exit_or_a_raise_errored_whether_or_not_the_claim_ended(
    tmp_path, released
):
    first = ["release"] if released else []
    prior = [died_body(2, K, "test: released")] if released else []
    with LocalLauncher(root=tmp_path) as launcher:
        for run, acts, expected in (
            ("signal", ["signal", str(int(signal.SIGKILL))], (K, "reap: signal 9")),
            ("exit", ["exit", "2"], (E, "reap: exit 2")),
        ):
            h = launcher.launch(run, child(*first, *acts))
            h.wait(timeout=30)
            ch = launcher.attach_channel(run)
            claim_seq = launched_claim(ch).seq
            assert undated(deaths(ch)) == [
                *[died_body(claim_seq, K, "test: released")] * released,
                died_body(claim_seq, *expected),
            ]
            ch.close()

    def raises(ch):
        body = asdict(Started(handle=LIVE, t=1.0, resume_from=None, sources=[]))
        seq = ch.send(
            body,
            topic=Topic.LIFECYCLE_STARTED,
            request_id=current_launch_id(),
            expected_seq=ch.last_seq(),
        )
        assert seq == 2
        if released:
            d = Died(claim_seq=seq, outcome=K, evidence="test: released", t=2.0)
            ch.send(asdict(d), topic=Topic.SUPERVISOR_DIED)
        raise RuntimeError("boom")

    threads = ThreadLauncher(root=tmp_path)
    h = threads.launch("raise", raises)
    h.wait()
    assert isinstance(h.exception, RuntimeError)
    ch = threads.attach_channel("raise")
    assert undated(deaths(ch)) == [
        *prior,
        died_body(2, E, "reap: RuntimeError: boom"),
    ]
    assert peek_terminal(ch).outcomes == ({K, E} if released else {E})


@pytest.mark.parametrize("released", [False, True], ids=["held", "released"])
def test_an_exit_0_or_a_return_reports_lost_while_held_and_nothing_after_a_release(
    tmp_path, released
):
    first = ["release"] if released else []
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("exit", child(*first, "exit", "0"))
        assert h.wait(timeout=30) == 0
        ch = launcher.attach_channel("exit")
        claim_seq = launched_claim(ch).seq
        assert terminated(ch) == [(h.launch_id, "exited", 0, None)]
        assert undated(deaths(ch)) == (
            [died_body(claim_seq, K, "test: released")]
            if released
            else [died_body(claim_seq, L, "reap: exit 0")]
        )
        ch.close()

    def returns(ch):
        body = asdict(Started(handle=LIVE, t=1.0, resume_from=None, sources=[]))
        seq = ch.send(
            body,
            topic=Topic.LIFECYCLE_STARTED,
            request_id=current_launch_id(),
            expected_seq=ch.last_seq(),
        )
        if released:
            d = Died(claim_seq=seq, outcome=K, evidence="test: released", t=2.0)
            ch.send(asdict(d), topic=Topic.SUPERVISOR_DIED)

    threads = ThreadLauncher(root=tmp_path)
    threads.launch("return", returns).wait()
    ch = threads.attach_channel("return")
    assert undated(deaths(ch)) == (
        [died_body(2, K, "test: released")]
        if released
        else [died_body(2, L, "reap: returned")]
    )


def test_local_launcher_reap_terminates_its_own_child_whose_claim_a_report_ended(
    tmp_path,
):
    """Only the launcher kills an orphan, through the ``Popen`` it holds, never by
    pid, and only once a death report has ended its claim (plan, open question
    6): a child whose claim is held is left alone."""
    with LocalLauncher(root=tmp_path) as launcher:
        orphan = launcher.launch("orphan", child("block"))
        held = launcher.launch("held", child("block"))
        try:
            ch = launcher.attach_channel("orphan")
            claim_seq = launched_claim(ch).seq
            launched_claim(launcher.attach_channel("held"))
            launcher.reap()
            assert orphan.is_alive() and held.is_alive()  # both claims are held
            report_death(ch, claim_seq, outcome=L, evidence="session: s/1", force=True)
            launcher.reap()
            assert orphan.wait(timeout=30) == -signal.SIGTERM
            assert held.is_alive()
            # its reap records the signal, whatever ended the claim before it
            assert undated(deaths(ch)) == [
                died_body(claim_seq, L, "session: s/1"),
                died_body(claim_seq, K, f"reap: signal {int(signal.SIGTERM)}"),
            ]
            ch.close()
        finally:
            for h in (orphan, held):
                if h.is_alive():
                    h.terminate()
                    h.wait(timeout=30)


# ----- the deciders ----------------------------------------------------------------


def test_relaunch_if_needed_and_ensure_served_report_a_dead_held_claim_then_launch(
    tmp_path,
):
    launcher = ThreadLauncher(root=tmp_path)
    handle = dead_handle()
    ran = []

    def target(ch, *, run):
        ran.append(run)

    def run_with(run_id, rows):
        append(launcher.create_channel(run_id), rows)
        return launcher.attach_channel(run_id)

    # relaunch_if_needed: a dead held claim is reported, then a worker launched
    ch = run_with("durable", [(1, claim(handle))])
    h = relaunch_if_needed(launcher, "durable", target, kwargs={"run": "durable"})
    assert h is not None
    h.wait()
    assert undated(deaths(ch)) == [died_body(1, L, f"probe: {handle}")]
    assert ch.read(after=2)[0].topic == Topic.SUPERVISOR_LAUNCHED
    # a live held claim: nothing reports, and nothing launches
    ch = run_with("live", [(1, claim(LIVE))])
    assert relaunch_if_needed(launcher, "live", target, kwargs={"run": "x"}) is None
    assert ch.last_seq() == 1

    # ensure_served: durable demand on a dead held claim is served
    rows = [
        (1, subscribe("lr", "durable", every={"commits": 1})),
        (2, claim(handle)),
    ]
    ch = run_with("served", rows)
    demand = LiveDemand()
    h = ensure_served(
        launcher, "served", target, demand=demand, kwargs={"run": "served"}
    )
    assert h is not None
    h.wait()
    assert undated(deaths(ch)) == [died_body(2, L, f"probe: {handle}")]
    # the only lease, bound to the dead claim: the report voids it, and the demand
    # read again after the report finds nothing to serve (§5)
    rows = [
        (1, subscribe(None, "lease", until={"time_seconds": 60})),
        (2, claim(handle)),
        (3, bound(2, "lease")),
    ]
    ch = run_with("leased", rows)
    demand = LiveDemand()
    assert [e.request_id for e in demand.poll(ch)] == ["lease"]
    assert ensure_served(launcher, "leased", target, demand=demand) is None
    assert undated(deaths(ch)) == [died_body(2, L, f"probe: {handle}")]
    assert demand.poll(ch) == [] and live_episode(ch) is None
    assert ran == ["durable", "served"]
    # a live held claim with demand: nothing reports, nothing launches
    rows = [(1, subscribe("lr", "d", every={"commits": 1})), (2, claim(LIVE))]
    ch = run_with("busy", rows)
    assert ensure_served(launcher, "busy", target, demand=LiveDemand()) is None
    assert ch.last_seq() == 2


def test_the_launchers_records_are_valid_0_4_0(tmp_path):
    """Every record the launchers and the helpers write conforms to supervisor-v0.5."""
    pytest.importorskip("jsonschema")
    from test_schema_v0_4_0 import record_errors

    with LocalLauncher(root=tmp_path) as launcher:
        launcher.launch("local", child("exit", "1")).wait(timeout=30)
        ch = launcher.attach_channel("local")
    threads = ThreadLauncher(root=tmp_path)

    def raises(c):
        body = asdict(Started(handle=LIVE, t=1.0, resume_from=None, sources=[]))
        c.send(body, topic=Topic.LIFECYCLE_STARTED, request_id=current_launch_id())
        raise ValueError("x")

    threads.launch("thread", raises).wait()
    probed = channel([(1, claim(dead_handle()))])
    report_if_dead(probed)
    for c in (ch, threads.attach_channel("thread"), probed):
        records = c.read(topics=["supervisor.>"])
        assert {e.topic for e in records} >= {Topic.SUPERVISOR_DIED}
        for e in records:
            assert record_errors(asdict(e)) == [], e
    ch.close()
