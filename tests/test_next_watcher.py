"""The Watcher, ``await_consumed`` and ``sweep``, staged (docs/specs/commits-and-lineage.md
§5, "The verdict", "Bounded reads", "``await_consumed``", "Sites that change"; §3, "A
read never writes", "A session probe"; observer-clock.md §5; §10, "``await_consumed``",
"Liveness", "Bounded reads", "Death reports"; plan Task 15).

Most logs are hand-written 0.4.0 (the rows of ``tests/test_next_reads.py``), so a poll
sees exactly what a test appended; the clock is injected wherever staleness is
measured. The Postgres tests use two channels on one run, a writer whose session the
test holds or closes, and an observer, as two hosts would.
"""

import sys
import threading
import time
import uuid
from dataclasses import asdict

import pytest

from runstate._next.checkpoint import NoSave
from runstate._next.launcher import (
    LocalLauncher,
    ThreadLauncher,
    ThreadTarget,
    report_death,
)
from runstate._next.observables import (
    Head,
    LatestClaimHead,
    RunResult,
    Tier,
    MalformedRecordError,
    prefix_len,
    undischarged_stops,
)
from runstate._next.sweep import Variant, sweep
from runstate._next.vocabulary.coordinate import Commits, Target
from runstate._next.vocabulary.payloads import (
    Heartbeat,
    Nak,
    NakReason,
    Outcome,
    Started,
    Topic,
)
from runstate._next.vocabulary.schedule import MalformedSchedule
from runstate._next.watcher import Running, Watcher, await_consumed
from runstate._next.worker import Worker
from runstate.channel import RunNotFound
from runstate.channel.base import Session
from runstate.channel.memory import MemoryChannel
from runstate.vocabulary.handle import local_handle
from tests.next_helpers import Wrapping
from tests.test_next_reads import (
    Rec,
    append,
    beat,
    channel,
    commit,
    died,
    exited,
    junk,
    launched,
    nak,
    rewind,
    started,
    stop,
    stopped,
    subscribe,
    value,
    withdraw,
)

pytestmark = pytest.mark.filterwarnings(
    "error::pytest.PytestUnhandledThreadExceptionWarning"
)

LIVE = local_handle()


def at(ch, *rows):
    """``rows`` appended from the log's next seq; the first one's seq."""
    first = ch.last_seq() + 1
    append(ch, list(enumerate(rows, start=first)))
    return first


def claim(*, t=1.0, resume_from=None, launch=None):
    return started(resume_from, t=t, launch=launch)


def watermark(claim_seq, consumed, *, t=2.5):
    """A heartbeat naming ``claim_seq`` whose drain read up to ``consumed``."""
    body = {"claim_seq": claim_seq, "consumed_seq": consumed, "t": t}
    return Rec("lifecycle.heartbeat", body, None, None)


def drained(claim_seq, parent, values, consumed, **kw):
    """A commit whose drain read up to ``consumed``."""
    r = commit(claim_seq, parent, values, **kw)
    return r._replace(body={**r.body, "consumed_seq": consumed})


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeHandle:
    """A ``LaunchHandle`` with a fixed liveness answer, whose reap writes nothing,
    as a handle of a launcher that records nothing would: ``waited`` counts reaps."""

    def __init__(self, run_id, channel, *, alive):
        self.run_id, self.channel, self.alive = run_id, channel, alive
        self.handle, self.launch_id, self.waited = "local://fake/0", "L1", 0

    def is_alive(self):
        return self.alive

    def wait(self, timeout=None):
        self.waited += 1

    def terminate(self):
        pass


class SessionChannel(Wrapping):
    """A memory channel with a session probe whose answer the test sets: every row
    has one writer, whose session ``ended`` says."""

    def __init__(self, inner):
        super().__init__(inner)
        self.ended = False
        self.asked = 0
        self.stamps = 0

    def writer_session(self, seq):
        self.stamps += 1
        return Session("srv/1", 7)

    def session_ended(self, session):
        self.asked += 1
        return self.ended


def watching(ch, *, timeout=None, clock=None, run_id="r"):
    w = Watcher(heartbeat_timeout=timeout, now=clock or Clock())
    w.observe(run_id, ch)
    return w


def presumed(claim_seq, tier, run_id="r"):
    return RunResult(
        outcomes=frozenset({Outcome.PRESUMED_DEAD}),
        reports=(),
        node=None,
        claim_seq=claim_seq,
        malformed=(),
        tier=tier,
        error=None,
        run_id=run_id,
    )


# ----- Running, and the verdict on the log -------------------------------------------


def test_running_head_and_commits_equal_the_pure_reads_after_every_poll():
    """``Running.head`` and ``Running.commits`` come from folds held per run (§5,
    "Sites that change"), and equal ``LatestClaimHead`` and ``prefix_len(Commits())``
    after every poll, across commits, a rewind, a new claim's resume and a displaced
    worker's commit. The first poll is a cold attach to a log already written, and a
    poll that returns the exit's verdict still advances the folds."""
    ch = channel([(1, claim()), (2, value("loss", 0.3)), (3, commit(1, None, [2]))])
    w = watching(ch)
    rows = [
        commit(1, 3, []),
        rewind(1, 3),
        commit(1, 3, []),
        stopped(1, 6, []),
        claim(resume_from=7),
        commit(1, 7, []),  # the displaced worker's: named, but not the claim's
        commit(8, 7, []),
        beat(8),
    ]
    for r in [None, *rows]:
        if r is not None:
            at(ch, r)
        s = w.poll("r")
        if isinstance(s, RunResult):  # the exit at 7, until the next claim
            assert (s.node, s.tier) == (7, Tier.EXIT)
            continue
        head = LatestClaimHead().resolve(ch)
        assert s.head == head
        assert s.commits == prefix_len(ch, Commits(), head=head)
    assert s.head == Head(10, False, 11) and s.commits == 3


def test_poll_is_running_then_the_verdict_stamped_with_the_run():
    """A verdict on the log is the poll's, stamped with the run the Watcher knows."""
    ch = channel([(1, claim()), (2, commit(1, None, []))])
    w = watching(ch)
    assert w.poll("r").done is False
    at(ch, stopped(1, 2, [], completed=True))
    r = w.poll("r")
    assert r.done is True and r.run_id == "r"
    assert (r.outcomes, r.tier, r.node, r.claim_seq) == (
        {Outcome.COMPLETED},
        Tier.EXIT,
        3,
        1,
    )


def test_a_verdict_on_the_log_beats_every_inference():
    """Even when the handle probes dead, the session has ended and the beacon is
    stale, a verdict on the log is the poll's."""
    ch = SessionChannel(MemoryChannel())
    at(ch, claim(t=0.0, launch="L1"), stopped(1, None, [], completed=True))
    ch.ended = True
    w = Watcher(heartbeat_timeout=1.0, now=Clock(1e6))
    w.add(FakeHandle("r", ch, alive=False))
    assert w.poll("r").outcomes == {Outcome.COMPLETED}


# ----- the inferences ----------------------------------------------------------------


def test_the_watchers_inferred_verdicts_carry_their_tier():
    """Each inference is ``presumed_dead`` naming the held latest claim, with its tier
    (§5): ``probed_dead`` for a held handle that probes dead and whose reap leaves no
    verdict; ``session_ended`` for a ``SessionProbe`` channel whose claim's session
    has ended, with no grace; ``heartbeat_stale`` past ``heartbeat_timeout``. Each
    satisfies ``RunResult``'s tier invariants: one outcome, its claim named, nothing a
    record would carry."""
    ch = channel([(1, claim(t=1000.0, launch="L1"))])
    w = Watcher(heartbeat_timeout=None, now=Clock())
    h = FakeHandle("r", ch, alive=False)
    w.add(h)
    assert w.poll("r") == presumed(1, Tier.PROBED_DEAD)
    assert h.waited == 1  # reaped before it presumed

    ch = SessionChannel(MemoryChannel())
    at(ch, claim(t=1000.0))
    w = watching(ch)
    assert w.poll("r").done is False
    ch.ended = True
    assert w.poll("r") == presumed(1, Tier.SESSION_ENDED)

    clock = Clock(1000.0)
    ch = channel([(1, claim(t=1000.0))])
    w = watching(ch, timeout=30, clock=clock)
    clock.t = 1031.0
    assert w.poll("r") == presumed(1, Tier.HEARTBEAT_STALE)


def test_a_worker_hung_before_it_claims_is_presumed_dead_past_the_timeout():
    """The startup-death catch (observer-clock §5; §5, ``claim_seq`` by tier): a run
    nothing has claimed is seeded at the Watcher's ``now``, as a beacon it never
    witnessed, so a worker that hangs before it claims goes ``heartbeat_stale``,
    naming no claim, once ``heartbeat_timeout`` passes: held and alive, or observed.
    A launch's ``launched`` is no beacon."""
    clock = Clock(1000.0)
    held, observed = MemoryChannel(), channel([(1, launched("L0"))])
    w = Watcher(heartbeat_timeout=30, now=clock)
    w.add(FakeHandle("h", held, alive=True))
    w.observe("o", observed)
    clock.t = 1030.0
    assert all(isinstance(w.poll(r), Running) for r in ("h", "o"))
    clock.t = 1031.0
    assert w.poll("h") == presumed(None, Tier.HEARTBEAT_STALE, run_id="h")
    assert w.poll("o") == presumed(None, Tier.HEARTBEAT_STALE, run_id="o")


def test_a_held_handle_dead_before_it_claims_is_probed_dead_until_its_reap_writes():
    """A held handle that probes dead before anything claims is ``probed_dead``,
    naming no claim, once its reap leaves no verdict (§5): the poll order, the
    verdict the log holds first. Its launch's ``terminated``, once one lands, is the
    verdict, ``terminated``, as an unclaimed run's is."""
    ch = MemoryChannel()
    w = Watcher(heartbeat_timeout=None, now=Clock(1e6))
    h = FakeHandle("r", ch, alive=False)
    w.add(h)
    assert w.poll("r") == presumed(None, Tier.PROBED_DEAD)
    assert h.waited == 1
    at(ch, exited("L1", 3))
    r = w.poll("r")
    assert (r.outcomes, r.tier, r.claim_seq) == (
        {Outcome.ERRORED},
        Tier.TERMINATED,
        None,
    )


def test_a_held_handle_reads_its_launchs_verdict_not_the_previous_claims():
    """A Watcher holding a launch's handle reads that launch's verdict (§5): the run
    already has an extendable exit, and the relaunch is ``Running`` until a claim
    naming its launch ends, a foreign claim's exit included. A Watcher observing the
    run with no handle reads the latest claim's, as ``peek_terminal`` does."""
    rows = [(1, claim(launch="L0")), (2, commit(1, None, [])), (3, stopped(1, 2, []))]
    ch = channel(rows)
    w = Watcher(heartbeat_timeout=30, now=Clock(1000.0))
    w.add(FakeHandle("r", ch, alive=True))  # launch L1
    assert isinstance(w.poll("r"), Running)
    foreign = at(ch, claim(launch="L9", t=1000.0))
    at(ch, stopped(foreign, None, [], completed=True))
    assert isinstance(w.poll("r"), Running)
    c = at(ch, claim(launch="L1", resume_from=3, t=1000.0))
    at(ch, commit(c, 3, [], t=1000.0))
    assert isinstance(w.poll("r"), Running)
    at(ch, stopped(c, c + 1, [], completed=True))
    r = w.poll("r")
    assert (r.claim_seq, r.tier, r.outcomes) == (c, Tier.EXIT, {Outcome.COMPLETED})

    w = watching(channel(rows))
    r = w.poll("r")
    assert (r.claim_seq, r.tier, r.outcomes) == (1, Tier.EXIT, {Outcome.PREEMPTED})


def test_the_session_tier_reads_a_claims_stamp_once():
    """The session tier is resolved once, at registration, for a ``SessionProbe``
    channel; a channel without one has no such tier. A claim's writer is fixed, so
    its stamp is read once; whether it has ended is asked on each poll."""
    ch = SessionChannel(MemoryChannel())
    at(ch, claim())
    w = watching(ch)
    for _ in range(3):
        w.poll("r")
    assert (ch.stamps, ch.asked) == (1, 3)
    plain = channel([(1, claim())])
    assert isinstance(watching(plain).poll("r"), Running)


# ----- witnessed liveness (observer-clock §5) -----------------------------------------


def test_a_started_a_commit_and_a_rewind_witness_liveness():
    """Liveness is witnessed (§3, §5): a new ``started``, or a ``commit``,
    ``heartbeat`` or ``rewind`` naming the latest claim, restarts the staleness clock
    at the Watcher's own ``now``. A displaced worker's commit, a value, a death
    report of another claim or a junk heartbeat earns no credit. The boundary is
    strict: an age equal to the timeout is alive."""
    clock = Clock(1000.0)
    ch = channel([(1, claim(t=1000.0))])
    w = watching(ch, timeout=30, clock=clock)

    def witnessed(r):
        at(ch, r)
        s = w.poll("r")
        assert isinstance(s, Running) and s.beacon_age == 0  # witnessed now
        clock.t += 30
        s = w.poll("r")
        assert isinstance(s, Running) and s.beacon_age == 30  # strict
        clock.t += 1
        assert w.poll("r").tier == Tier.HEARTBEAT_STALE

    witnessed(commit(1, None, []))
    witnessed(rewind(1, None))
    witnessed(beat(1))
    witnessed(claim(t=0.0))  # a new claim: seq 5
    for unwitnessed in (
        commit(1, None, []),  # the displaced worker's
        value("loss", 0.1),
        junk(beat(5), t="junk"),  # names the claim, but is no heartbeat
    ):
        at(ch, unwitnessed)
        assert w.poll("r").tier == Tier.HEARTBEAT_STALE


def test_a_cold_attach_reads_the_true_age_from_the_seeds_t():
    """The seed is the record's own ``t``, the newest of the latest claim's
    ``started``, commits, heartbeats and rewinds (§5), so a cold attach to a run
    that went quiet long ago reads its true age on the first poll. A future-dated
    seed reads a negative age, alive: the safe direction."""
    ch = channel(
        [(1, claim(t=100.0)), (2, commit(1, None, [], t=150.0)), (3, beat(1, t=200.0))]
    )
    w = watching(ch, timeout=30, clock=Clock(1000.0))
    assert w.poll("r") == presumed(1, Tier.HEARTBEAT_STALE)
    w = watching(ch, timeout=None, clock=Clock(1000.0))
    assert w.poll("r").beacon_age == 800.0
    w = watching(ch, timeout=30, clock=Clock(150.0))
    s = w.poll("r")
    assert isinstance(s, Running) and s.beacon_age == -50.0


def test_a_fresh_record_after_a_cold_seed_is_witnessed_at_arrival():
    """Seeded old and stale, a genuinely newer record is timed at its arrival, the
    Watcher's ``now``, never its own ``t``: skew-immune."""
    clock = Clock(1000.0)
    ch = channel([(1, claim(t=100.0))])
    w = watching(ch, timeout=30, clock=clock)
    assert w.poll("r").done is True
    at(ch, beat(1, t=200.0))
    assert isinstance(w.poll("r"), Running)
    clock.t = 1031.0
    assert w.poll("r").done is True


def test_a_junk_record_earns_no_liveness_credit():
    """A junk record naming the claim is no measurement (§5): it earns no credit,
    witnessed or seeded, and never crashes a poll. The seed passes over it to the
    newest readable record."""
    ch = channel(
        [
            (1, claim(t=100.0)),
            (2, beat(1, t=900.0)),
            (3, junk(beat(1, t=990.0), consumed_seq="x")),
        ]
    )
    w = watching(ch, timeout=None, clock=Clock(1000.0))
    assert w.poll("r").beacon_age == 100.0  # from seq 2, not the junk at 3


class LandsAClaimMidRead(MemoryChannel):
    """Once ``armed``, lands a claim right after its next read of claims returns,
    ``latest`` or ``read`` alike, as a claim CAS'd between a reader's two reads."""

    armed = False

    def _land(self):
        if self.armed:
            self.armed = False
            append(self, [(self.last_seq() + 1, claim(t=1000.0))])

    def latest(self, topic, name=None):
        e = super().latest(topic, name)
        if topic == Topic.LIFECYCLE_STARTED:
            self._land()
        return e

    def read(self, after=0, *, topics=None, name=None, request_ids=None, limit=None):
        got = super().read(
            after, topics=topics, name=name, request_ids=request_ids, limit=limit
        )
        if topics is not None and Topic.LIFECYCLE_STARTED in topics:
            self._land()
        return got


def test_a_claim_landing_during_registration_is_witnessed():
    """Registration reads W first, then the newest claim at or below it, so a claim
    CAS'd between the two reads lands above the cursor and the first poll witnesses
    it: a claim that heartbeats is never presumed stale."""
    ch = LandsAClaimMidRead()
    at(ch, claim(t=1000.0))
    ch.armed = True
    clock = Clock(1000.0)
    w = watching(ch, timeout=5.0, clock=clock)
    assert ch.armed is False  # the claim at seq 2 landed during registration
    for _ in range(5):
        clock.t += 3.0
        at(ch, beat(2, t=clock.t))
        assert isinstance(w.poll("r"), Running)


def test_staleness_counts_from_the_claims_started():
    """A claim that never writes after its ``started`` is stale from its ``t``: a
    startup death or hang is caught, not only a hang mid-run."""
    clock = Clock(1000.0)
    ch = channel([(1, claim(t=1000.0))])
    w = watching(ch, timeout=30, clock=clock)
    clock.t = 1010.0
    assert w.poll("r").done is False
    clock.t = 1031.0
    assert w.poll("r").tier == Tier.HEARTBEAT_STALE


def test_no_staleness_tier_without_a_timeout():
    """``heartbeat_timeout=None`` is no staleness tier: an old run reads alive."""
    ch = channel([(1, claim(t=0.0))])
    assert isinstance(watching(ch, timeout=None, clock=Clock(1e9)).poll("r"), Running)


def test_watcher_poll_and_is_alive_write_nothing_but_a_reap_of_a_held_handle(tmp_path):
    """A read never writes (§3): every tier's poll leaves the log as it was, a dead
    handle in the claim included, which only ``report_if_dead`` reports. The one
    write is a reap of a handle the Watcher holds, through its launcher, which here
    writes the launch's ``terminated`` and the ``died`` its exit code is evidence of,
    so the verdict is the precise one."""
    from tests.test_next_death_reports import CHILD, dead_handle, launched_claim

    ch = SessionChannel(MemoryChannel())
    body = {**claim(t=0.0).body, "handle": dead_handle()}
    ch.send(body, topic=Topic.LIFECYCLE_STARTED)
    w = Watcher(heartbeat_timeout=1.0, now=Clock(1e6))
    w.observe("r", ch)
    for ended in (False, True):
        ch.ended = ended
        assert w.poll("r").done is True
    assert ch.last_seq() == 1

    with LocalLauncher(root=tmp_path) as launcher:
        from runstate._next.launcher import Command

        h = launcher.launch(
            "local", Command([sys.executable, "-c", CHILD, "block"], env={})
        )
        launched_claim(h.channel)
        w = Watcher(heartbeat_timeout=None)
        w.add(h)
        before = h.channel.last_seq()
        assert h.is_alive() and isinstance(w.poll("local"), Running)
        assert h.channel.last_seq() == before
        h.terminate()
        r = w.wait("local")
    assert (r.outcomes, r.tier) == ({Outcome.KILLED}, Tier.DIED)
    after = launcher.attach_channel("local").read(after=before)
    assert [e.topic for e in after] == [
        Topic.SUPERVISOR_TERMINATED,
        Topic.SUPERVISOR_DIED,
    ]


def test_the_watcher_reaps_a_crashed_launch_for_a_precise_verdict(tmp_path):
    """A child that exits 42 before claiming: the Watcher's reap writes its
    ``terminated``, and the unclaimed run's verdict is ``errored``, not a bare
    presumption."""
    with LocalLauncher(root=tmp_path) as launcher:
        from runstate._next.launcher import Command

        cmd = Command([sys.executable, "-c", "import os; os._exit(42)"], env={})
        h = launcher.launch("crash", cmd)
        w = Watcher(heartbeat_timeout=None, poll_interval=0.005)
        w.add(h)
        r = w.wait("crash")
    assert (r.outcomes, r.tier) == ({Outcome.ERRORED}, Tier.TERMINATED)
    term = launcher.attach_channel("crash").latest(Topic.SUPERVISOR_TERMINATED)
    assert term.body["exit_code"] == 42


# ----- the session probe on Postgres --------------------------------------------------


def _pg(dsn, run_id):
    from runstate.channel.postgres import PostgresChannel

    return PostgresChannel(dsn, run_id=run_id)


def _claim_on(ch, *, t=1.0):
    body = asdict(Started(handle=LIVE, t=t, resume_from=None, sources=[]))
    return ch.send(body, topic=Topic.LIFECYCLE_STARTED, expected_seq=ch.last_seq())


def test_a_held_claim_whose_session_ended_is_presumed_dead_without_a_grace(pg_ready):
    """A held claim whose writer's session has ended is ``session_ended`` (§5; plan,
    open question 16), with no grace: a channel takes its key before its first write,
    so the claim's row was stamped by a session held when it landed. While the
    session is held, the run reads alive, a live pid on the same host
    notwithstanding either way."""
    import time

    run_id = f"wsession-{uuid.uuid4()}"
    writer, obs = _pg(pg_ready, run_id), _pg(pg_ready, run_id)
    try:
        seq = _claim_on(writer)
        w = Watcher(heartbeat_timeout=None, now=lambda: 0.0)
        w.observe(run_id, obs)
        assert isinstance(w.poll(run_id), Running)
        writer.close()
        deadline = time.monotonic() + 10
        while not (s := w.poll(run_id)).done:  # the server releases the lock
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert s == presumed(seq, Tier.SESSION_ENDED, run_id)
        assert obs.last_seq() == seq  # a poll writes nothing
    finally:
        writer.close()
        obs.close()


def test_the_staleness_floor_is_not_vetoed_by_a_held_session(pg_ready):
    """A held session only abstains: a worker connected but no longer writing still
    goes ``heartbeat_stale``."""
    run_id = f"wfloor-{uuid.uuid4()}"
    writer, obs = _pg(pg_ready, run_id), _pg(pg_ready, run_id)
    try:
        seq = _claim_on(writer, t=0.0)
        beat_body = asdict(Heartbeat(claim_seq=seq, consumed_seq=0, t=0.0))
        writer.send(beat_body, topic=Topic.LIFECYCLE_HEARTBEAT)
        clock = Clock(0.0)
        w = Watcher(heartbeat_timeout=1.0, now=clock)
        w.observe(run_id, obs)
        assert isinstance(w.poll(run_id), Running)
        clock.t = 2.0
        assert w.poll(run_id) == presumed(seq, Tier.HEARTBEAT_STALE, run_id)
    finally:
        writer.close()
        obs.close()


def test_a_workers_claim_is_stamped_with_its_held_session(pg_ready):
    """The staged Worker's claim row carries the session of the channel that wrote it,
    held while the worker runs: an observer reads it alive by the session probe, with
    no step of the Worker's own (the episode lock it once held is gone, §11)."""
    run_id = f"wworker-{uuid.uuid4()}"
    wch, obs = _pg(pg_ready, run_id), _pg(pg_ready, run_id)
    try:
        w = Worker(wch, resume_from=None, sources=[], save=NoSave())
        assert w.claimed
        claim_seq = obs.latest(Topic.LIFECYCLE_STARTED).seq
        session = obs.writer_session(claim_seq)
        assert session is not None and obs.session_ended(session) is False
    finally:
        obs.close()
        wch.close()


# ----- wait, wait_all, iter_events ----------------------------------------------------


def done_worker(ch, *, n=2):
    with Worker(ch, resume_from=None, sources=[], save=NoSave()) as w:
        for _ in w.steps(Target(Commits(), n)):
            pass
        w.stopped(completed=True, error=None)


def thread(fn, **kwargs):
    return ThreadTarget(fn, args=(), kwargs=kwargs)


def test_wait_blocks_until_terminal():
    launcher = ThreadLauncher()
    w = Watcher(heartbeat_timeout=None, poll_interval=0.005)
    w.add(launcher.launch("run", thread(done_worker, n=3)))
    r = w.wait("run")
    assert r.outcomes == {Outcome.COMPLETED} and r.run_id == "run"


def test_wait_timeout_raises():
    clock = Clock(0.0)
    w = Watcher(
        heartbeat_timeout=None,
        now=clock,
        sleep=lambda s: setattr(clock, "t", clock.t + s),
        poll_interval=1.0,
    )
    w.observe("slow", MemoryChannel())
    with pytest.raises(TimeoutError):
        w.wait("slow", timeout=5.0)


def test_iter_events_streams_then_continues_from_cursor():
    ch = MemoryChannel()
    w = watching(ch)
    at(ch, value("x", 1), claim())
    assert [(r, e.topic) for r, e in w.iter_events(timeout=0)] == [
        ("r", "value"),
        ("r", "lifecycle.started"),
    ]
    at(ch, value("y", 2))
    assert [(r, e.topic) for r, e in w.iter_events(timeout=0)] == [("r", "value")]


def test_iter_events_spans_multiple_runs():
    a, b = MemoryChannel(), MemoryChannel()
    w = Watcher(heartbeat_timeout=None)
    w.observe("a", a)
    w.observe("b", b)
    at(a, value("x", 1))
    at(b, value("y", 2))
    assert {r for r, _ in w.iter_events(timeout=0)} == {"a", "b"}


def test_wait_streams_events_via_on_event():
    launcher = ThreadLauncher()
    w = Watcher(heartbeat_timeout=None, poll_interval=0.005)
    w.add(launcher.launch("run", thread(done_worker)))
    seen = []
    r = w.wait("run", on_event=lambda rid, e: seen.append(e.topic))
    assert r.outcomes == {Outcome.COMPLETED}
    assert {"lifecycle.started", "lifecycle.commit", "lifecycle.stopped"} <= set(seen)


def test_wait_does_a_final_drain_after_terminal():
    """A record landing as the verdict is reached still reaches ``on_event``."""
    ch = channel([(1, claim()), (2, stopped(1, None, [], completed=True))])
    w = watching(ch)
    seen = []

    def on_event(rid, e):
        seen.append(e.topic)
        if e.topic == "lifecycle.stopped" and len(seen) == 2:
            at(ch, exited("L1", 0))

    w.wait("r", on_event=on_event)
    assert "supervisor.terminated" in seen


def test_wait_all_returns_all_terminal():
    launcher = ThreadLauncher()
    w = Watcher(heartbeat_timeout=None, poll_interval=0.005)
    w.add(launcher.launch("a", thread(done_worker, n=1)))
    w.add(launcher.launch("b", thread(done_worker, n=1)))
    res = w.wait_all()
    assert set(res) == {"a", "b"}
    assert all(s.done and s.outcomes == {Outcome.COMPLETED} for s in res.values())


def test_wait_all_capped_reports_pending_as_running():
    """At its deadline, a run still in flight is its ``Running`` status: where it is
    and how long since it was witnessed alive."""
    clock = Clock(0.0)
    ch = channel([(1, claim(t=0.0)), (2, commit(1, None, [], t=0.0))])
    w = Watcher(
        heartbeat_timeout=None,
        now=clock,
        sleep=lambda s: setattr(clock, "t", clock.t + s),
        poll_interval=1.0,
    )
    w.observe("a", ch)
    res = w.wait_all(timeout=5.0)
    s = res["a"]
    assert isinstance(s, Running)
    assert (s.head.node, s.commits, s.beacon_age) == (2, 1, 5.0)


# ----- broadcast and pending_stops ----------------------------------------------------


def test_broadcast_fans_subscription_with_shared_request_id():
    a, b = MemoryChannel(), MemoryChannel()
    w = Watcher(heartbeat_timeout=None)
    w.observe("a", a)
    w.observe("b", b)
    rid = w.broadcast("loss", {"every": {"commits": 1}})
    for ch in (a, b):
        sub = ch.latest(Topic.CONTROL_SUBSCRIBE)
        assert (sub.name, sub.request_id, sub.body) == (
            "loss",
            rid,
            {"every": {"commits": 1}},
        )
    assert w.broadcast("loss", {"every": {"commits": 2}}, request_id="mine") == "mine"


def test_broadcast_takes_none_for_pure_demand():
    """``broadcast(None, ...)`` sends a pure demand, a null ``name`` (§5). The schedule
    is parsed first, as a pure demand's for ``None``, so a malformed broadcast raises
    ``MalformedSchedule`` and writes nothing on any run; an empty id is nameless."""
    a, b = MemoryChannel(), MemoryChannel()
    w = Watcher(heartbeat_timeout=None)
    w.observe("a", a)
    w.observe("b", b)
    rid = w.broadcast(None, {"until": {"commits": 5}})
    for ch in (a, b):
        sub = ch.latest(Topic.CONTROL_SUBSCRIBE)
        assert (sub.name, sub.request_id) == (None, rid)
    for name, bad in (
        (None, {"every": {"commits": 1}}),  # a pure demand takes only an until
        ("loss", {"every": {"step": 1}}),  # 0.3.0's step atom
    ):
        with pytest.raises(MalformedSchedule):
            w.broadcast(name, bad)
    with pytest.raises(ValueError, match="empty"):
        w.broadcast("loss", {"every": {"commits": 1}}, request_id="")
    assert a.last_seq() == b.last_seq() == 1


def test_pending_stops_is_the_held_undischarged_stops():
    ch = channel([(1, stop("s1")), (2, stop("s2"))])
    w = watching(ch)
    assert [e.seq for e in w.pending_stops("r")] == [1, 2]
    at(ch, withdraw("s1"))
    assert w.pending_stops("r") == undischarged_stops(ch)


# ----- await_consumed (§5) ------------------------------------------------------------


def request_log(*, kind="subscribe"):
    """A held claim, then the request at seq 2."""
    req = (
        subscribe("loss", "r", every={"commits": 1})
        if kind == "subscribe"
        else stop("r")
    )
    return channel([(1, claim()), (2, req)])


@pytest.mark.parametrize(
    "answer",
    [
        lambda: watermark(1, 2),
        lambda: drained(1, None, [], 2),
        lambda: commit(1, None, [], fired=["r"]),
        lambda: commit(1, None, [], expired=["r"]),
    ],
    ids=["heartbeat-watermark", "commit-watermark", "fired", "expired"],
)
def test_await_consumed_accepts_by_a_watermark_or_an_answer(answer):
    """``None``, accepted, once a claim's watermark reaches the request (a commit's
    or a heartbeat's ``consumed_seq``) or an answer names it (§5): a node's ``fired``
    or ``expired``, an exit's ``honored``. A watermark below it waits."""
    ch = request_log()
    at(ch, watermark(1, 1))  # below the request
    sleep = lambda _: at(ch, answer())  # noqa: E731
    assert await_consumed(ch, 2, sleep=sleep) is None


def test_await_consumed_accepts_a_stop_its_ending_exit_honored():
    """A stop the claim honored in its exit was carried out: accepted, never the
    verdict, though no watermark follows."""
    ch = request_log(kind="stop")
    at(ch, stopped(1, None, [], honored=["r"]))
    assert await_consumed(ch, 2, timeout=1.0) is None


def test_await_consumed_accepts_an_expiry_on_another_lineage():
    """An ``expired`` anywhere naming the request counts as accepted: "did my request
    land" asks whether a worker took it, on whichever lineage."""
    ch = request_log()
    at(
        ch,
        commit(1, None, []),
        commit(1, 3, [], expired=["r"]),
        rewind(1, 3),  # the head leaves the node that expired it
    )
    assert await_consumed(ch, 2, timeout=1.0) is None


def test_await_consumed_returns_the_nak_by_name_answer_first():
    """A refusal naming the request returns its ``Nak``, by the request's own id,
    before any watermark: a retiring worker's ``nak`` may be its last record. A
    refusal of another id answers nothing; the caller passes no id."""
    ch = request_log()
    at(ch, nak("other"), nak("r", reason="unsatisfiable"))
    got = await_consumed(ch, 2, timeout=1.0)
    assert got == Nak(reason=NakReason.UNSATISFIABLE, message="no")
    with pytest.raises(TypeError):
        await_consumed(ch, 2, request_id="other", timeout=1.0)


def test_await_consumed_typed_error_on_a_malformed_nak():
    ch = request_log()
    seq = at(ch, junk(nak("r"), message=None))
    with pytest.raises(MalformedRecordError) as ei:
        await_consumed(ch, 2, timeout=1.0)
    assert (ei.value.seq, ei.value.topic) == (seq, "lifecycle.nak")


def test_await_consumed_ignores_a_junk_watermark():
    """A heartbeat whose body does not parse is no watermark evidence: it waits."""
    ch = request_log()
    at(ch, junk(watermark(1, 2), t="junk"))
    with pytest.raises(TimeoutError):
        await_consumed(ch, 2, timeout=0.0, now=lambda: 0.0)


def test_await_consumed_returns_the_verdict_for_a_request_the_ending_exit_saw_and_did_not_take():
    """The latest claim ended by its own exit above the request, and no watermark of
    it reaches the request: the exit was CAS-appended at a tail its writer had read,
    so the claim saw the request and ended without taking it. The verdict."""
    ch = request_log()
    at(ch, watermark(1, 1), stopped(1, None, []))
    r = await_consumed(ch, 2, timeout=1.0)
    assert isinstance(r, RunResult)
    assert (r.outcomes, r.tier, r.claim_seq) == ({Outcome.PREEMPTED}, Tier.EXIT, 1)


def test_a_request_posted_after_the_claim_ended_waits_for_the_next_claim():
    """A request above the latest claim's exit waits for the next claim, which takes
    it (here by its first commit's watermark): a death before the request is no
    refusal of it."""
    ch = channel(
        [
            (1, claim()),
            (2, stopped(1, None, [])),
            (3, subscribe("loss", "r", every={"commits": 1})),
        ]
    )
    with pytest.raises(TimeoutError):
        await_consumed(ch, 3, timeout=0.0, now=lambda: 0.0)

    def next_claim(_):
        if ch.last_seq() == 3:
            at(ch, claim(resume_from=2))
        else:
            at(ch, drained(4, 2, [], 4))

    assert await_consumed(ch, 3, sleep=next_claim) is None


def test_a_request_above_a_dead_claims_last_watermark_waits_though_a_died_follows_it():
    """A death report above the request shows that its writer read the request, not
    that the dead claim did: the request waits for the next claim."""
    ch = request_log()
    at(ch, watermark(1, 1), died(1, "killed"))
    with pytest.raises(TimeoutError):
        await_consumed(ch, 2, timeout=0.0, now=lambda: 0.0)


def test_await_consumed_returns_the_verdict_on_a_closed_head():
    """On a closed head nothing will ever take the request, though it follows the
    closing exit: the terminal verdict."""
    ch = channel([(1, claim()), (2, stopped(1, None, [], completed=True))])
    at(ch, subscribe("loss", "r", every={"commits": 1}))
    r = await_consumed(ch, 3, timeout=1.0)
    assert isinstance(r, RunResult) and r.outcomes == {Outcome.COMPLETED}


def test_await_consumed_blocks_below_the_watermark_then_returns_when_it_advances():
    ch = request_log()
    at(ch, watermark(1, 1))
    turns = []

    def advance(_):
        turns.append(1)
        at(ch, watermark(1, 2))

    assert await_consumed(ch, 2, sleep=advance) is None and turns == [1]


def test_await_consumed_times_out_if_not_consumed():
    ch = request_log()
    with pytest.raises(TimeoutError):
        await_consumed(ch, 2, timeout=0.0, now=lambda: 0.0)


@pytest.mark.parametrize(
    "spend",
    [
        lambda: nak("r"),
        lambda: withdraw("r"),
        lambda: stopped(1, None, [], honored=["r"]),
    ],
    ids=["nak", "withdraw", "honored"],
)
@pytest.mark.parametrize("kind", ["subscribe", "stop"])
def test_await_consumed_refuses_a_request_reusing_a_spent_id(spend, kind):
    """Request ids are one namespace, and a spent id is dead on arrival on every
    control topic (§2), so a request reusing one is skipped unanswered: ``ValueError``
    up front, naming the record that spent it."""
    req = (
        subscribe("loss", "r", every={"commits": 1})
        if kind == "subscribe"
        else stop("r")
    )
    ch = channel(
        [(1, claim()), (2, spend()), (3, claim()), (4, req), (5, watermark(3, 4))]
    )
    with pytest.raises(ValueError, match=r"'r'.* at seq 2\b"):
        await_consumed(ch, 4, timeout=1.0)


@pytest.mark.parametrize(
    "row, match",
    [
        (lambda: value("loss", 1.0), "not a control request"),
        (lambda: withdraw("r"), "not a control request"),
        (lambda: Rec("control.pause", {}, None, "r"), "not a control request"),
        (lambda: stop(None), "names no request"),
        (lambda: stop(""), "names no request"),
        (lambda: subscribe("loss", "", every={"commits": 1}), "names no request"),
    ],
    ids=[
        "value",
        "withdraw",
        "unknown-verb",
        "stop-no-id",
        "stop-empty-id",
        "subscribe-empty-id",
    ],
)
def test_await_consumed_refuses_what_no_worker_answers(row, match):
    """Up front, before any wait: a record that is not a subscribe or a stop (a
    ``control.withdraw`` needs no worker), and a nameless request (a missing or empty
    id), though a watermark passes it: ``None`` would be a false "accepted"."""
    ch = channel([(1, claim()), (2, row()), (3, watermark(1, 2))])
    with pytest.raises(ValueError, match=match):
        await_consumed(ch, 2, timeout=1.0)


def test_await_consumed_refuses_a_seq_that_holds_no_request():
    ch = channel([(1, claim()), (2, watermark(1, 999))])
    with pytest.raises(ValueError, match="no record at seq 9"):
        await_consumed(ch, 9, timeout=0.0, now=lambda: 0.0)


# The void lease: a lease is bound to its claim, and void once that claim has ended.

LEASE = {"every": {"commits": 1}, "until": {"time_seconds": 100}}


def lease(ch, rid):
    return ch.send(LEASE, topic=Topic.CONTROL_SUBSCRIBE, name="loss", request_id=rid)


def serving(ch):
    """A worker serving the source ``loss``, its register set."""
    w = Worker(ch, resume_from=None, sources=["loss"], save=NoSave(), now=lambda: 0.0)
    assert w.claimed
    w.set("loss", 1.0)
    return w


def bound_to_an_ended_claim():
    """E1 binds the lease ``r1``, then exits."""
    ch = MemoryChannel()
    lease(ch, "r1")
    w1 = serving(ch)
    w1.commit()
    w1.stopped(completed=False, error=None)
    return ch


def test_await_consumed_refuses_a_same_id_resend_after_the_bound_claim_ended():
    """A lease bound to a claim that has ended is void: every later claim drops a
    resend of it, unanswered, so ``ValueError`` up front, naming the claim, by its
    exit or by a third party's death report alike."""
    ch = bound_to_an_ended_claim()
    w2 = serving(ch)
    s = lease(ch, "r1")
    w2.commit()  # E2 drains it past the watermark, and drops it
    with pytest.raises(ValueError, match=r"claim \d+.*fresh request_id"):
        await_consumed(ch, s, timeout=1.0)
    assert [b.request_id for b in ch.read(topics=[Topic.LIFECYCLE_BOUND])] == ["r1"]

    ch = MemoryChannel()
    lease(ch, "r1")
    w1 = serving(ch)
    w1.commit()
    report_death(
        ch,
        ch.latest(Topic.LIFECYCLE_STARTED).seq,
        outcome=Outcome.KILLED,
        evidence="test: released",
        force=True,
    )
    s = lease(ch, "r1")
    with pytest.raises(ValueError, match="fresh request_id"):
        await_consumed(ch, s, timeout=1.0)


def test_await_consumed_accepts_a_fresh_id_after_the_bound_claim_ended():
    ch = bound_to_an_ended_claim()
    w2 = serving(ch)
    s = lease(ch, "r2")
    w2.commit()
    assert await_consumed(ch, s, timeout=1.0) is None


def test_await_consumed_accepts_a_same_id_resend_within_the_bound_claim():
    ch = MemoryChannel()
    lease(ch, "r1")
    w1 = serving(ch)
    w1.commit()  # binds r1 to E1
    s = lease(ch, "r1")  # a renewal, E1 still holding it
    w1.commit()
    assert await_consumed(ch, s, timeout=1.0) is None


def test_await_consumed_waits_on_an_unbound_lease():
    ch = MemoryChannel()
    s = lease(ch, "r1")  # no worker yet: nothing bound it
    with pytest.raises(TimeoutError):
        await_consumed(ch, s, timeout=0.0, now=lambda: 0.0)


def test_await_consumed_does_not_accept_a_lease_voided_while_it_waits():
    """The bound claim ends while the caller waits, and the next claim's watermark
    passes the resend it dropped: never "accepted"."""
    ch = MemoryChannel()
    lease(ch, "r1")
    w1 = serving(ch)
    w1.commit()
    s = lease(ch, "r1")  # a renewal, not yet drained
    turns = []

    def end_and_pass(_):
        if turns:
            return
        turns.append(1)
        w1.stopped(completed=False, error=None)
        serving(ch).commit()

    with pytest.raises(ValueError, match="fresh request_id"):
        await_consumed(ch, s, sleep=end_and_pass)


def test_await_consumed_by_backend(open_run):
    """``await_consumed`` over every backend, a writer and an observer on two
    handles: refused, then accepted."""
    ch = open_run()
    c = at(ch, claim())
    s = at(ch, subscribe("loss", "r", until={"commits": 0}))
    at(ch, nak("r", reason="unsatisfiable"), watermark(c, s))
    got = await_consumed(open_run(), s, timeout=1.0)
    assert isinstance(got, Nak) and got.reason == NakReason.UNSATISFIABLE
    s = at(ch, stop("halt"))
    at(ch, watermark(c, s))
    assert await_consumed(open_run(), s, timeout=1.0) is None


# ----- sweep ----------------------------------------------------------------------


def fast():
    return Watcher(heartbeat_timeout=None, poll_interval=0.005)


def boom(ch):
    with Worker(ch, resume_from=None, sources=[], save=NoSave()) as w:
        for _ in w.steps(Target(Commits(), 1)):
            raise RuntimeError("boom")


def sweep_of(launcher, variants, **kw):
    kw = {"watcher": fast(), "resume": True, "stop_on_failure": False, **kw}
    return sweep(variants, launcher, **kw)


def test_sweep_runs_all_variants_and_returns_results(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    results = sweep_of(
        launcher, [Variant(f"run-{i}", thread(done_worker)) for i in range(3)]
    )
    assert [r.run_id for r in results] == ["run-0", "run-1", "run-2"]
    assert all(r.outcomes == {Outcome.COMPLETED} for r in results)


def test_sweep_streams_events_via_on_event(tmp_path):
    seen = []
    sweep_of(
        ThreadLauncher(root=tmp_path),
        [Variant("r", thread(done_worker))],
        on_event=lambda rid, e: seen.append((rid, e.topic)),
    )
    assert ("r", "lifecycle.stopped") in seen


def test_sweep_resume_skips_a_run_with_a_verdict(tmp_path):
    launcher = ThreadLauncher(root=tmp_path, backend="sqlite")
    calls = []

    def counted(ch):
        calls.append(1)
        done_worker(ch)

    variants = [Variant("r", thread(counted))]
    sweep_of(launcher, variants)
    results = sweep_of(launcher, variants)
    assert calls == [1]
    assert results[0].outcomes == {Outcome.COMPLETED} and results[0].run_id == "r"
    sweep_of(launcher, variants, resume=False)
    assert calls == [1, 1]


def test_sweep_stop_on_failure_tests_the_set(tmp_path):
    """``stop_on_failure`` halts once a verdict has any outcome in
    ``Outcome.failures()``, the set tested whole (§5): a run whose death reports say
    ``{killed, preempted}`` halts, and so does an errored exit; a clean preempted exit
    does not. A variant after the halt is never launched."""
    launcher = ThreadLauncher(root=tmp_path, backend="sqlite")
    # b's log already holds two death reports: resumed, its verdict is their set
    append(
        launcher.create_channel("b"),
        [(1, claim()), (2, died(1, "preempted")), (3, died(1, "killed"))],
    )

    def preempted(ch):
        with Worker(ch, resume_from=None, sources=[], save=NoSave()) as w:
            w.commit()

    results = sweep_of(
        launcher,
        [
            Variant("a", thread(preempted)),
            Variant("b", thread(done_worker)),
            Variant("c", thread(done_worker)),
        ],
        stop_on_failure=True,
    )
    assert [r.run_id for r in results] == ["a", "b"]
    assert results[0].outcomes == {Outcome.PREEMPTED}
    assert results[1].outcomes == {Outcome.KILLED, Outcome.PREEMPTED}
    with pytest.raises(RunNotFound):
        launcher.attach_channel("c")
    results = sweep_of(
        launcher,
        [Variant("d", thread(boom)), Variant("e", thread(done_worker))],
        stop_on_failure=True,
    )
    assert [r.outcomes for r in results] == [{Outcome.ERRORED}]


def test_sweep_does_not_halt_on_a_clean_commanded_stop(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)

    def commanded(ch):
        ch.send({}, topic=Topic.CONTROL_STOP, request_id="x")
        with Worker(ch, resume_from=None, sources=[], save=NoSave()) as w:
            for _ in w.steps(Target(Commits(), 5)):
                pass

    results = sweep_of(
        launcher,
        [Variant("a", thread(commanded)), Variant("b", thread(done_worker))],
        stop_on_failure=True,
    )
    assert results[0].outcomes == {Outcome.PREEMPTED}
    assert [r.run_id for r in results] == ["a", "b"]


def test_sweep_does_not_hang_on_a_worker_stuck_before_it_claims():
    """A worker that hangs before it claims is presumed dead once the Watcher's
    ``heartbeat_timeout`` passes (the startup-death catch), so the sweep returns."""
    gate = threading.Event()
    results = []

    def hung(ch):
        gate.wait(30)

    def run():
        w = Watcher(heartbeat_timeout=0.2, poll_interval=0.01)
        results.extend(
            sweep_of(ThreadLauncher(), [Variant("h", thread(hung))], watcher=w)
        )

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(10)
    hanging = t.is_alive()
    gate.set()
    t.join(10)
    assert not hanging, "sweep hung on a worker stuck before it claims"
    (r,) = results
    assert (r.outcomes, r.tier, r.claim_seq) == (
        {Outcome.PRESUMED_DEAD},
        Tier.HEARTBEAT_STALE,
        None,
    )


def test_a_relaunching_sweep_waits_for_the_new_launchs_own_verdict(tmp_path):
    """A sweep that relaunches a run whose claim exited extendable (``resume=False``:
    ``resume`` skips a run with any verdict) watches the new launch's handle, so it
    returns that launch's verdict, never the previous claim's."""
    launcher = ThreadLauncher(root=tmp_path, backend="sqlite")

    def preempted(ch):
        with Worker(ch, resume_from=None, sources=[], save=NoSave()) as w:
            w.commit()

    def late(ch):
        time.sleep(0.2)
        done_worker(ch)

    (first,) = sweep_of(launcher, [Variant("r", thread(preempted))])
    assert first.outcomes == {Outcome.PREEMPTED}
    (r,) = sweep_of(launcher, [Variant("r", thread(late))], resume=False)
    assert r.outcomes == {Outcome.COMPLETED} and r.claim_seq > first.claim_seq
