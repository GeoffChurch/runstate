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
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict

import pytest

from runstate._next.launcher import (
    REPORT_ATTEMPTS,
    ClaimAliveError,
    Command,
    LocalLauncher,
    ReportStarvedError,
    ThreadLauncher,
    ThreadTarget,
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
from runstate._next.vocabulary.coordinate import Commits, Target
from runstate._next.vocabulary.payloads import Died, Outcome, Started, Topic
from runstate.channel.memory import MemoryChannel
from runstate.vocabulary.handle import local_handle, local_handle_of
from runstate.vocabulary.launch import current_launch_id
from tests.test_next_reads import Rec, append, bound, channel, died, stopped, subscribe

# A launcher's thread that raises is a test failure, never a warning.
pytestmark = pytest.mark.filterwarnings(
    "error::pytest.PytestUnhandledThreadExceptionWarning"
)

K, E, L, P = Outcome.KILLED, Outcome.ERRORED, Outcome.LOST, Outcome.PREEMPTED

LIVE = local_handle()  # this process: probes alive
FOREIGN = "local://not-this-host/1"  # another host: the probe abstains


def dead_handle():
    """A handle on this host whose process has exited and been reaped: probes dead.
    (pid reuse could revive it, the limit §12 names; within a test it does not.)"""
    p = subprocess.Popen(["true"])
    p.wait()
    return local_handle_of(p.pid)


def claim(handle, *, launch=None, t=1.0):
    body = {"handle": handle, "t": t, "resume_from": None, "sources": []}
    return Rec("lifecycle.started", body, None, launch)


def claim_launch(ch):
    """A thread target's claim: a readable 0.4.0 ``lifecycle.started`` naming its
    launch, CAS-appended; its seq."""
    body = asdict(Started(handle=LIVE, t=1.0, resume_from=None, sources=[]))
    return ch.send(
        body,
        topic=Topic.LIFECYCLE_STARTED,
        request_id=current_launch_id(),
        expected_seq=ch.last_seq(),
    )


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


def test_report_death_is_presence_based_and_never_raises_on_a_contradictory_window():
    """Two readable exits contradict §2's one per claim: the verdict's to raise,
    never a reporter's (§3, "Its tests are presence-based and never raise"). The
    claim has a readable exit, so a report of either kind returns ``None``."""
    ch = channel(
        [(1, claim(FOREIGN)), (2, stopped(1, None, [])), (3, stopped(1, None, []))]
    )
    for outcome in (L, K):
        assert report_death(ch, 1, outcome=outcome, evidence="x", force=True) is None
    assert ch.last_seq() == 3


def test_report_death_on_an_unreadable_claim_does_not_refuse():
    """A claim whose ``started`` is unreadable has no handle to probe, which does
    not refuse, as an unknown probe does not (§3)."""
    ch = channel([(1, Rec("lifecycle.started", {"handle": 5}, None, None))])
    assert report_death(ch, 1, outcome=K, evidence="slurm:x", force=False) == 2
    assert undated(deaths(ch)) == [died_body(1, K, "slurm:x")]


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


def test_a_report_that_never_wins_its_cas_raises_report_starved():
    """A claimant that writes faster than the report's read, probe and append round
    trip: the report's CAS loses every time, and it gives up, typed, after a
    bounded number of attempts rather than spinning forever."""

    class Outrun(MemoryChannel):
        cas = 0

        def send(self, body, *, topic, name=None, request_id=None, expected_seq=None):
            if expected_seq is not None:
                self.cas += 1
                if self.cas > 10 * REPORT_ATTEMPTS:
                    raise AssertionError("an unbounded CAS loop")
                beat = {"claim_seq": 1, "consumed_seq": 0, "t": 2.0}
                super().send(beat, topic=Topic.LIFECYCLE_HEARTBEAT)
            return super().send(
                body,
                topic=topic,
                name=name,
                request_id=request_id,
                expected_seq=expected_seq,
            )

    ch = Outrun()
    append(ch, [(1, claim(FOREIGN))])
    with pytest.raises(ReportStarvedError) as exc:
        report_death(ch, 1, outcome=K, evidence="slurm:x", force=True)
    assert (exc.value.claim_seq, exc.value.attempts) == (1, REPORT_ATTEMPTS)
    assert ch.cas == REPORT_ATTEMPTS
    assert deaths(ch) == []


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


def test_report_if_dead_never_raises_on_a_contradictory_window_or_an_unreadable_claim():
    """Held is presence-based (§5's ``ended``), and an unreadable claim has no
    handle to probe: either way the probe's report is ``None``, never a raise."""
    handle = dead_handle()
    ch = channel(
        [(1, claim(handle)), (2, stopped(1, None, [])), (3, stopped(1, None, []))]
    )
    assert report_if_dead(ch) is None
    ch = channel([(1, Rec("lifecycle.started", {"handle": 5}, None, None))])
    assert report_if_dead(ch) is None
    assert ch.last_seq() == 1


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
#   ignoreterm   it ignores SIGTERM, as a requeue handler may
#   badexit      it writes an unreadable exit naming its claim (it is finishing)
#   block        it sleeps until something kills it
CHILD = """
import os, signal, sys, time
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
    elif act == "ignoreterm":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    elif act == "badexit":
        ch.send({"claim_seq": claim, "completed": "yes"}, topic="lifecycle.stopped")
    elif act == "block":
        time.sleep(120)
"""

# A worker whose claim, naming its launch, is unreadable; it sleeps until killed.
UNREADABLE_CLAIM = """
import time
from runstate import current_channel
from runstate.vocabulary.launch import current_launch_id

ch = current_channel()
ch.send({"handle": 7}, topic="lifecycle.started", request_id=current_launch_id())
time.sleep(120)
"""


def child(*acts):
    """The child's command, with no environment of its own."""
    return Command([sys.executable, "-c", CHILD, *acts], env={})


def thread(fn, **kwargs):
    """A thread target with no positional arguments."""
    return ThreadTarget(fn, args=(), kwargs=kwargs)


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
    wrapper = Command(
        ["sh", "-c", f'"{sys.executable}" "{script}" exit 0; exit 3'], env={}
    )
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


def test_local_launchers_reap_is_not_stopped_by_one_runs_unreadable_claim(tmp_path):
    """One run's unreadable claim neither raises out of ``reap()`` nor keeps it from
    reaping another child (§3, "Its tests are presence-based and never raise"). An
    unreadable ``started`` names no claimant, so its child's reap writes
    ``terminated`` only."""
    script = tmp_path / "unreadable.py"
    script.write_text(UNREADABLE_CLAIM)
    with LocalLauncher(root=tmp_path) as launcher:
        bad = launcher.launch("bad", Command([sys.executable, str(script)], env={}))
        good = launcher.launch("good", child("exit", "2"))
        bad_ch = launcher.attach_channel("bad")
        ch = launcher.attach_channel("good")
        try:
            launched_claim(bad_ch)
            good._proc.wait(timeout=30)  # exited, and not yet reaped
            for _ in range(3):
                launcher.reap()
            claim_seq = launched_claim(ch).seq
            assert terminated(ch) == [(good.launch_id, "exited", 2, None)]
            assert undated(deaths(ch)) == [died_body(claim_seq, E, "reap: exit 2")]
            assert bad.is_alive() and terminated(bad_ch) == []
            bad.terminate()
            assert bad.wait(timeout=30) == -signal.SIGTERM
            assert terminated(bad_ch) == [(bad.launch_id, "killed", None, 15)]
            assert deaths(bad_ch) == []
        finally:
            ch.close()
            bad_ch.close()
            if bad.is_alive():
                bad._proc.kill()
                bad.wait(timeout=30)


@pytest.mark.parametrize(
    "failing", [Topic.SUPERVISOR_TERMINATED, Topic.SUPERVISOR_DIED]
)
def test_local_launchers_reap_retries_a_write_that_failed(
    tmp_path, monkeypatch, failing
):
    """A write the reap could not land is retried by the next ``poll()``: the exit
    status ``waitpid`` yielded once is kept on the handle, with the reap's one
    ``t``, and a record that landed is not written twice."""
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("run", child("exit", "3"))
        h._proc.wait(timeout=30)
        sent = h.channel.send
        fails = [failing]

        def flaky(body, *, topic, **kw):
            if fails and topic == fails[0]:
                fails.pop()
                raise OSError("the backend went away")
            return sent(body, topic=topic, **kw)

        monkeypatch.setattr(h.channel, "send", flaky)
        with pytest.raises(OSError):
            h.poll()
        assert h.poll() == 3
        h.wait()  # reaped: nothing more
        ch = launcher.attach_channel("run")
        try:
            claim_seq = launched_claim(ch).seq
            assert terminated(ch) == [(h.launch_id, "exited", 3, None)]
            assert undated(deaths(ch)) == [died_body(claim_seq, E, "reap: exit 3")]
            end, report = (
                ch.latest(topic).body["t"]
                for topic in (Topic.SUPERVISOR_TERMINATED, Topic.SUPERVISOR_DIED)
            )
            assert end == report  # one reap, one t
        finally:
            ch.close()


def test_local_launchers_reap_processes_every_handle_and_raises_what_failed_after(
    tmp_path, monkeypatch
):
    """The backstop: a handle whose backend raises does not keep ``reap()`` from
    the others; the errors are raised together once every handle is processed,
    each noted with the run, the launch and the handle it failed on."""
    with LocalLauncher(root=tmp_path) as launcher:
        broken = launcher.launch("broken", child("block"))
        good = launcher.launch("good", child("exit", "2"))
        broken_ch = launcher.attach_channel("broken")
        ch = launcher.attach_channel("good")
        try:
            launched_claim(broken_ch)
            good._proc.wait(timeout=30)

            def unreachable(*a, **kw):
                raise OSError("the backend went away")

            monkeypatch.setattr(broken.channel, "read", unreachable)
            with pytest.raises(ExceptionGroup) as group:
                launcher.reap()
            assert [type(e) for e in group.value.exceptions] == [OSError]
            assert group.value.exceptions[0].__notes__ == [
                f"run 'broken', launch {broken.launch_id}, {broken.handle}"
            ]
            assert terminated(ch) == [(good.launch_id, "exited", 2, None)]
            assert len(deaths(ch)) == 1
        finally:
            ch.close()
            broken_ch.close()
            monkeypatch.undo()
            if broken.is_alive():
                broken._proc.kill()
                broken.wait(timeout=30)


def _broken_reap(launcher, monkeypatch):
    """A launch whose reap raises: its child has exited, and its channel's every
    send raises, as does every fresh channel's (the backend is gone)."""
    h = launcher.launch("broken", child("exit", "0"))
    h._proc.wait(timeout=30)

    def gone(*a, **kw):
        raise OSError("the backend went away")

    monkeypatch.setattr(h.channel, "send", gone)
    monkeypatch.setattr(h, "_reopen", gone)
    return h


def test_a_launchers_exit_notes_its_reaps_failures_on_the_bodys_exception(
    tmp_path, monkeypatch
):
    """The body's exception propagates, never the reap's group, which would hide
    the cause behind its consequence; the reap's failures ride on it as notes."""
    with pytest.raises(KeyError) as body:
        with LocalLauncher(root=tmp_path) as launcher:
            h = _broken_reap(launcher, monkeypatch)
            raise KeyError("the body")
    [note] = body.value.__notes__
    assert "OSError('the backend went away')" in note
    assert f"run 'broken', launch {h.launch_id}, {h.handle}" in note


def test_a_launchers_exit_raises_its_reaps_failures_when_the_body_did_not(
    tmp_path, monkeypatch
):
    with pytest.raises(ExceptionGroup) as group:
        with LocalLauncher(root=tmp_path) as launcher:
            _broken_reap(launcher, monkeypatch)
    assert [type(e) for e in group.value.exceptions] == [OSError]


def test_thread_launcher_writes_died_when_its_thread_finishes_with_no_exit(tmp_path):
    def claims(ch, *, exits):
        seq = claim_launch(ch)
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
    h = launcher.launch("no-exit", thread(claims, exits=False))
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
    h = launcher.launch("exited", thread(claims, exits=True))
    h.wait()
    ch = launcher.attach_channel("exited")
    assert deaths(ch) == [] and terminated(ch) == [(h.launch_id, "exited", 0, None)]
    # and a launch nothing claimed writes its terminated only, its run's verdict
    h = launcher.launch("unclaimed", thread(lambda ch: None))
    h.wait()
    ch = launcher.attach_channel("unclaimed")
    assert deaths(ch) == [] and peek_terminal(ch).tier == Tier.TERMINATED


def test_thread_launchers_end_never_raises_on_an_unreadable_claim_or_two_exits(
    tmp_path, monkeypatch
):
    """The thread's end writes its records on any log: an unreadable claim naming
    the launch still ends by a report (no handle is probed, and none need be), and
    a claim with two readable exits has one, so the raise adds nothing."""
    raised = []
    monkeypatch.setattr(threading, "excepthook", raised.append)

    def unreadable(ch):
        ch.send(
            {"handle": 7}, topic=Topic.LIFECYCLE_STARTED, request_id=current_launch_id()
        )

    def exits_twice(ch):
        seq = claim_launch(ch)
        for _ in range(2):
            r = stopped(seq, None, [])
            ch.send(r.body, topic=r.topic)
        raise RuntimeError("boom")

    launcher = ThreadLauncher(root=tmp_path)
    h = launcher.launch("unreadable", thread(unreadable))
    h.wait()
    ch = launcher.attach_channel("unreadable")
    assert terminated(ch) == [(h.launch_id, "exited", 0, None)]
    assert undated(deaths(ch)) == [died_body(2, L, "reap: returned")]
    h = launcher.launch("twice", thread(exits_twice))
    h.wait()
    ch = launcher.attach_channel("twice")
    assert terminated(ch) == [(h.launch_id, "exited", 1, None)]
    assert deaths(ch) == []
    assert raised == []


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
        seq = claim_launch(ch)
        assert seq == 2
        if released:
            d = Died(claim_seq=seq, outcome=K, evidence="test: released", t=2.0)
            ch.send(asdict(d), topic=Topic.SUPERVISOR_DIED)
        raise RuntimeError("boom")

    threads = ThreadLauncher(root=tmp_path)
    h = threads.launch("raise", thread(raises))
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
        seq = claim_launch(ch)
        if released:
            d = Died(claim_seq=seq, outcome=K, evidence="test: released", t=2.0)
            ch.send(asdict(d), topic=Topic.SUPERVISOR_DIED)

    threads = ThreadLauncher(root=tmp_path)
    threads.launch("return", thread(returns)).wait()
    ch = threads.attach_channel("return")
    assert undated(deaths(ch)) == (
        [died_body(2, K, "test: released")]
        if released
        else [died_body(2, L, "reap: returned")]
    )


def test_local_launcher_reap_kills_its_own_child_whose_claim_a_report_ended(
    tmp_path,
):
    """Only the launcher kills an orphan, through the ``Popen`` it holds, never by
    pid, and only once a death report has ended its claim (plan, open question
    6): a child whose claim is held is left alone. The kill is ``SIGKILL``, and
    the orphan's reap writes ``terminated`` only: the signal is the launcher's,
    no evidence of how the claim ended (§3, "A session probe")."""
    with LocalLauncher(root=tmp_path) as launcher:
        orphan = launcher.launch("orphan", child("block"))
        held = launcher.launch("held", child("block"))
        ch = launcher.attach_channel("orphan")
        held_ch = launcher.attach_channel("held")
        try:
            claim_seq = launched_claim(ch).seq
            launched_claim(held_ch)
            launcher.reap()
            assert orphan.is_alive() and held.is_alive()  # both claims are held
            report_death(ch, claim_seq, outcome=L, evidence="session: s/1", force=True)
            launcher.reap()
            assert orphan.wait(timeout=30) == -signal.SIGKILL
            assert held.is_alive()
            assert terminated(ch) == [
                (orphan.launch_id, "killed", None, int(signal.SIGKILL))
            ]
            assert undated(deaths(ch)) == [died_body(claim_seq, L, "session: s/1")]
            assert peek_terminal(ch).outcomes == {L}
        finally:
            ch.close()
            held_ch.close()
            for h in (orphan, held):
                if h.is_alive():
                    h._proc.kill()
                    h.wait(timeout=30)


def test_local_launcher_kills_an_orphan_that_ignores_sigterm(tmp_path):
    """A child that handles ``SIGTERM``, as a requeue handler does, would live
    beside its successor: the orphan is killed, not asked (§3)."""
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("orphan", child("ignoreterm", "release", "block"))
        ch = launcher.attach_channel("orphan")
        try:
            until(lambda: deaths(ch) != [])
            launcher.reap()
            assert h.wait(timeout=10) == -signal.SIGKILL
        finally:
            ch.close()
            if h.is_alive():
                h._proc.kill()
                h.wait(timeout=30)


def test_local_launcher_does_not_kill_a_child_its_own_unreadable_exit_ended(
    tmp_path, monkeypatch
):
    """An orphan is a child whose claim a ``died`` names and no ``stopped`` does,
    readable or not: a claim its own exit ended is finishing (§3)."""
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("finishing", child("badexit", "block"))
        ch = launcher.attach_channel("finishing")
        try:
            until(lambda: ch.latest(Topic.LIFECYCLE_STOPPED))
            assert peek_terminal(ch).tier == Tier.DIED  # its unreadable exit ended it
            kills = []
            with monkeypatch.context() as m:
                m.setattr(h._proc, "kill", lambda: kills.append(h._proc.pid))
                launcher.reap()
            assert kills == [] and h.is_alive()
        finally:
            ch.close()
            if h.is_alive():
                h._proc.kill()
                h.wait(timeout=30)


# ----- the targets: each launcher launches its own record ---------------------------


def test_a_launcher_launches_its_own_target_record(tmp_path):
    """``ThreadLauncher`` runs a ``ThreadTarget``'s ``fn(channel, *args, **kwargs)``;
    ``LocalLauncher`` spawns a ``Command``'s ``argv`` with its ``env`` over
    ``os.environ`` and under the ``RUNSTATE_*`` variables. Each record is a value:
    the caller's later mutation does not reach it, and neither can the worker's."""
    seen = []
    kwargs = {"b": 2}
    target = ThreadTarget(
        lambda ch, *a, **kw: seen.append((a, kw)), args=[1], kwargs=kwargs
    )
    kwargs["b"] = 3  # the record copied it
    assert target.args == (1,) and target.kwargs == {"b": 2}
    with pytest.raises(TypeError):
        target.kwargs["b"] = 4  # read-only
    ThreadLauncher(root=tmp_path).launch("thread", target).wait()
    assert seen == [((1,), {"b": 2})]

    check = (
        "import os, sys; e = os.environ; "
        "sys.exit(0 if e['X'] == '1' and e['RUNSTATE_RUN_ID'] == 'local' "
        "and 'PATH' in e else 5)"
    )
    env = {"X": "1", "RUNSTATE_RUN_ID": "spoofed"}
    cmd = Command([sys.executable, "-c", check], env=env)
    env["X"] = "2"  # the record copied it
    assert cmd.argv == (sys.executable, "-c", check) and cmd.env["X"] == "1"
    with LocalLauncher(root=tmp_path) as launcher:
        assert launcher.launch("local", cmd).wait(timeout=30) == 0
    with pytest.raises(TypeError, match="not a string"):
        Command(sys.executable, env={})  # would split into its characters
    with pytest.raises(ValueError, match="program"):
        Command([], env={})


def test_a_thread_target_is_driven_by_a_copy_whose_kwargs_carry_until(tmp_path):
    """``driven(until)`` is ``Drivable``'s one member: a copy whose ``kwargs`` gain
    ``until``, the ``Target`` the Worker's ``steps(until)`` takes. The original is
    unchanged, and a target whose ``kwargs`` already carry an ``until`` refuses,
    since a drive's target is the drive's."""
    seen = []

    def fn(ch, *args, **kwargs):
        seen.append((args, kwargs))

    target = ThreadTarget(fn, args=(1,), kwargs={"a": 2})
    until = Target(Commits(), 3)
    driven = target.driven(until)
    assert driven == ThreadTarget(fn, args=(1,), kwargs={"a": 2, "until": until})
    assert target.kwargs == {"a": 2}
    ThreadLauncher(root=tmp_path).launch("driven", driven).wait()
    assert seen == [((1,), {"a": 2, "until": until})]
    with pytest.raises(ValueError, match="until is the drive's"):
        driven.driven(Target("loss", 4))


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
    h = relaunch_if_needed(launcher, "durable", thread(target, run="durable"))
    assert h is not None
    h.wait()
    assert undated(deaths(ch)) == [died_body(1, L, f"probe: {handle}")]
    assert ch.read(after=2)[0].topic == Topic.SUPERVISOR_LAUNCHED
    # a live held claim: nothing reports, and nothing launches
    ch = run_with("live", [(1, claim(LIVE))])
    assert relaunch_if_needed(launcher, "live", thread(target, run="x")) is None
    assert ch.last_seq() == 1

    # ensure_served: durable demand on a dead held claim is served
    rows = [
        (1, subscribe("lr", "durable", every={"commits": 1})),
        (2, claim(handle)),
    ]
    ch = run_with("served", rows)
    demand = LiveDemand()
    h = ensure_served(launcher, "served", thread(target, run="served"), demand=demand)
    assert h is not None
    h.wait()
    assert undated(deaths(ch)) == [died_body(2, L, f"probe: {handle}")]
    assert ran == ["durable", "served"]
    # a live held claim with demand: nothing reports, nothing launches
    rows = [(1, subscribe("lr", "d", every={"commits": 1})), (2, claim(LIVE))]
    ch = run_with("busy", rows)
    assert (
        ensure_served(launcher, "busy", thread(target, run="busy"), demand=LiveDemand())
        is None
    )
    assert ch.last_seq() == 2


def test_ensure_served_reads_demand_again_after_a_report_voids_the_only_lease(
    tmp_path,
):
    """The only lease is bound to the dead held claim: the report ends the claim,
    which voids the lease (§2, a lease is per claim), and the demand read again
    after the report finds nothing to serve, so nothing launches (§5)."""
    launcher = ThreadLauncher(root=tmp_path)
    handle = dead_handle()
    rows = [
        (1, subscribe(None, "lease", until={"time_seconds": 60})),
        (2, claim(handle)),
        (3, bound(2, "lease")),
    ]
    append(launcher.create_channel("leased"), rows)
    ch = launcher.attach_channel("leased")
    demand = LiveDemand()
    assert [e.request_id for e in demand.poll(ch)] == ["lease"]
    assert (
        ensure_served(launcher, "leased", thread(lambda ch: None), demand=demand)
        is None
    )
    assert undated(deaths(ch)) == [died_body(2, L, f"probe: {handle}")]
    assert demand.poll(ch) == [] and live_episode(ch) is None
    assert ch.read(topics=[Topic.SUPERVISOR_LAUNCHED]) == []


def test_the_launchers_records_are_valid_0_4_0(tmp_path):
    """Every record the launchers and the helpers write conforms to supervisor-v0.5."""
    pytest.importorskip("jsonschema")
    from test_schema_v0_4_0 import record_errors

    with LocalLauncher(root=tmp_path) as launcher:
        launcher.launch("local", child("exit", "1")).wait(timeout=30)
        ch = launcher.attach_channel("local")
    threads = ThreadLauncher(root=tmp_path)

    def raises(c):
        claim_launch(c)
        raise ValueError("x")

    threads.launch("thread", thread(raises)).wait()
    probed = channel([(1, claim(dead_handle()))])
    report_if_dead(probed)
    for c in (ch, threads.attach_channel("thread"), probed):
        records = c.read(topics=["supervisor.>"])
        assert {e.topic for e in records} >= {Topic.SUPERVISOR_DIED}
        for e in records:
            assert record_errors(asdict(e)) == [], e
    ch.close()


# ----- the local handle's reap: a latched kill, a locked reap, a fresh channel ------


def exited_unreaped(h):
    """Block until ``h``'s child has exited, leaving it unreaped (a zombie): its
    ``Popen`` has not seen the exit."""
    os.waitid(os.P_PID, h._proc.pid, os.WEXITED | os.WNOWAIT)


def test_a_second_kill_of_a_dying_orphan_leaves_its_reap_the_launchers(tmp_path):
    """The kill is latched: a later cycle's kill of a child already dying from the
    first leaves its reap writing ``terminated`` only (§3, "A session probe")."""
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("orphan", child("block"))
        ch = launcher.attach_channel("orphan")
        try:
            claim_seq = launched_claim(ch).seq
            report_death(ch, claim_seq, outcome=L, evidence="session: s/1", force=True)
            h._kill()
            exited_unreaped(h)
            h._kill()  # a later cycle's kill: the child is dead and unreaped
            assert h.poll() == -signal.SIGKILL
            assert terminated(ch) == [(h.launch_id, "killed", None, 9)]
            assert undated(deaths(ch)) == [died_body(claim_seq, L, "session: s/1")]
        finally:
            ch.close()


def test_a_natural_exit_racing_the_kill_is_the_childs_own_end(tmp_path, monkeypatch):
    """The child exits between ``Popen``'s own check and its signal: the kill was
    sent, but the exit status is the child's, not a ``SIGKILL``, so its reap is
    evidence and reports it."""
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("run", child("exit", "3"))
        ch = launcher.attach_channel("run")
        try:
            claim_seq = launched_claim(ch).seq
            exited_unreaped(h)
            with monkeypatch.context() as m:
                m.setattr(h._proc, "poll", lambda: None)  # Popen's check: running
                h._kill()
            assert h.poll() == 3
            assert terminated(ch) == [(h.launch_id, "exited", 3, None)]
            assert undated(deaths(ch)) == [died_body(claim_seq, E, "reap: exit 3")]
        finally:
            ch.close()


def test_concurrent_reaps_write_one_terminated_and_one_died(tmp_path, monkeypatch):
    """``wait()`` on one thread and ``poll()`` on another reap one child: the reap
    is locked, so each record lands once."""
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("run", child("exit", "3"))
        h._proc.wait(timeout=30)
        sent = h.channel.send

        def slow(body, **kw):
            time.sleep(0.1)  # a slow backend widens the window
            return sent(body, **kw)

        monkeypatch.setattr(h.channel, "send", slow)
        reapers = [threading.Thread(target=h.wait), threading.Thread(target=h.poll)]
        for t in reapers:
            t.start()
        for t in reapers:
            t.join()
        ch = launcher.attach_channel("run")
        try:
            assert terminated(ch) == [(h.launch_id, "exited", 3, None)]
            assert len(deaths(ch)) == 1
        finally:
            ch.close()


def test_a_reap_whose_write_raised_retries_on_a_fresh_channel(tmp_path, monkeypatch):
    """A channel never reconnects (specs/channel-postgres.md): once its connection
    has dropped, every send on it raises. The reap's retry opens a fresh channel
    for the run, and lands there."""
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("run", child("exit", "3"))
        h._proc.wait(timeout=30)
        dropped = h.channel

        def gone(*a, **kw):
            raise OSError("the connection dropped")

        monkeypatch.setattr(dropped, "send", gone)
        with pytest.raises(OSError):
            h.poll()
        assert h.poll() == 3
        assert h.channel is not dropped
        ch = launcher.attach_channel("run")
        try:
            claim_seq = launched_claim(ch).seq
            assert terminated(ch) == [(h.launch_id, "exited", 3, None)]
            assert undated(deaths(ch)) == [died_body(claim_seq, E, "reap: exit 3")]
        finally:
            ch.close()


def test_reap_drops_the_handles_it_has_reaped_and_closes_their_channels(
    tmp_path, monkeypatch
):
    with LocalLauncher(root=tmp_path) as launcher:
        done = launcher.launch("done", child("exit", "0"))
        running = launcher.launch("running", child("block"))
        try:
            done._proc.wait(timeout=30)
            closed = []
            close = done.channel.close
            monkeypatch.setattr(
                done.channel, "close", lambda: (closed.append(True), close())
            )
            launcher.reap()
            assert len(launcher._handles) == 1 and launcher._handles[0] is running
            assert closed == [True]
        finally:
            running._proc.kill()
            running.wait(timeout=30)


def test_is_alive_binds_the_reaps_time_when_it_sees_the_death(tmp_path):
    """``is_alive()`` writes nothing, but when the ``Popen`` reaps the exited child
    there, the reap's one ``t`` is bound: the records a later reap writes are dated
    by when the death was first seen (observer-clock §3)."""
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("run", child("exit", "0"))
        until(lambda: not h.is_alive())
        seen = h._death.t
        time.sleep(0.05)
        assert h.poll() == 0
        ch = launcher.attach_channel("run")
        try:
            assert ch.latest(Topic.SUPERVISOR_TERMINATED).body["t"] == seen
            assert ch.latest(Topic.SUPERVISOR_DIED).body["t"] == seen
        finally:
            ch.close()
