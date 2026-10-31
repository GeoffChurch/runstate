"""``ensure``, the producer seam and ``history``, staged (docs/specs/commits-and-lineage.md
§5, "The reads": ``history`` and ``ensure``; §3, "The writers": ``ensure`` reports on
each poll; §10, "``ensure``" and "Reads"; plan Task 14).

Most logs here are hand-written 0.4.0, appended by a fake producer's ``extend`` or by
the ``sleep`` hook ``ensure`` calls between polls, so each test fixes exactly what a
poll sees. A drive's handle is a ``Handle``: its ``launch_id`` names the claim the
drive's worker writes, and ``alive`` is what it says, whatever the log says. The
end-to-end tests drive the staged Worker through a ``ThreadLauncher`` and
``launch_producer``, as a consumer does.
"""

import subprocess
import time

import pytest

from runstate import LOG_FORMAT
from runstate._next.checkpoint import NoSave
from runstate._next.launcher import ThreadLauncher
from runstate._next.memoizer import (
    NoProgressError,
    Reached,
    RecordlessExitError,
    RunFailedError,
    Unreachable,
    ensure,
    foreign_episode,
    history,
    launch_producer,
)
from runstate._next.observables import (
    AtNode,
    Element,
    LatestClaimHead,
    MalformedRecordError,
    Tier,
    prefix_len,
    series,
    streams,
)
from runstate._next.vocabulary.coordinate import Commits, Target
from runstate._next.vocabulary.payloads import Outcome, Topic
from runstate._next.vocabulary.schedule import MalformedSchedule
from runstate._next.worker import Worker
from runstate.channel.memory import MemoryChannel
from runstate.formats import FORMATS
from runstate.vocabulary.handle import local_handle, local_handle_of
from tests.next_helpers import bounded_sleep, thread
from tests.test_next_reads import (
    append,
    channel,
    commit,
    died,
    exited,
    junk,
    rewind,
    started,
    stopped,
    value,
)

LIVE = local_handle()  # this process: probes alive

pytestmark = pytest.mark.filterwarnings(
    "error::pytest.PytestUnhandledThreadExceptionWarning"
)


def dead_handle():
    """A handle on this host whose process has exited and been reaped: probes dead
    (pid reuse could revive it, §12's limit; within a test it does not)."""
    p = subprocess.Popen(["true"])
    p.wait()
    return local_handle_of(p.pid)


def claim(*, launch=None, handle=LIVE, resume_from=None, sources=(), t=1.0):
    """A ``lifecycle.started`` naming the launch ``launch``, with ``handle``."""
    r = started(resume_from, sources=sources, t=t, launch=launch)
    return r._replace(body={**r.body, "handle": handle})


def at(ch, *rows):
    """``rows`` appended from the log's next seq: each seq is its index."""
    first = ch.last_seq() + 1
    append(ch, list(enumerate(rows, start=first)))
    return first


class Handle:
    """A drive's liveness handle: its ``launch_id``, and ``alive``, which the test
    sets. ``waited`` counts ``wait()``, the reap ``ensure`` calls once it sees the
    handle dead."""

    def __init__(self, launch_id, *, alive=False):
        self.launch_id = launch_id
        self.alive = alive
        self.waited = 0

    def is_alive(self):
        return self.alive

    def wait(self):
        self.waited += 1


class Fake:
    """A producer whose ``extend`` runs the next of ``drives``, each a function of
    the channel and the target returning the drive's handle; past the last it
    raises, so a test that would re-drive forever fails."""

    def __init__(self, ch, *drives, run_id="fake"):
        self.run_id = run_id
        self._ch = ch
        self._drives = list(drives)
        self.extends = []

    @property
    def channel(self):
        return self._ch

    def extend(self, target):
        self.extends.append(target)
        if len(self.extends) > len(self._drives):
            raise AssertionError(f"an unexpected drive #{len(self.extends)}")
        return self._drives[len(self.extends) - 1](self._ch, target)


def after(n, fn):
    """A ``sleep`` hook that calls ``fn()`` on its ``n``-th call, and fails past
    ``200`` calls, so a test that would wait forever fails."""
    calls = {"n": 0}

    def sleep(_):
        calls["n"] += 1
        if calls["n"] == n:
            fn()
        if calls["n"] > 200:
            raise AssertionError("ensure waits forever")

    return sleep


def never(_):
    raise AssertionError("ensure waited")


class Paced:
    """A ``sleep`` hook for a test whose only waits are the re-drives' pacing: it
    records each wait with the drives made by then, so a test asserts every wait
    precedes a re-drive, and none is a wait on a held claim."""

    def __init__(self, producer):
        self._producer = producer
        self.waits = []

    def __call__(self, seconds):
        self.waits.append((seconds, len(self._producer.extends)))


LOSS3 = Target("loss", 3)


# ----- read first, and Unreachable ----------------------------------------------------


def three_losses_preempted():
    """A claims, commits three ``loss`` elements, and exits preempted."""
    return channel(
        [
            (1, claim()),
            (2, value("loss", 0.3)),
            (3, commit(1, None, [2])),
            (4, value("loss", 0.2)),
            (5, commit(1, 3, [4])),
            (6, value("loss", 0.1)),
            (7, commit(1, 5, [6])),
            (8, stopped(1, 7, [])),
        ]
    )


def test_ensure_reads_first():
    """``count(x, head) >= n`` returns ``Reached`` at the head without a drive (§5,
    "Read first"), for an eager stream and for ``Commits()``; a second call is the
    same read (the cache hit)."""
    ch = three_losses_preempted()
    p = Fake(ch)
    assert ensure(p, LOSS3, sleep=never) == Reached(8)
    assert ensure(p, Target("loss", 1), sleep=never) == Reached(8)
    assert ensure(p, Target(Commits(), 3), sleep=never) == Reached(8)
    assert p.extends == []


def test_ensure_returns_unreachable_without_producing_on_a_closed_head():
    """A head closed below ``n`` is ``Unreachable`` at it, with no drive (§5): what
    0.3.0's "never re-drive a completed run" generalizes, and the launch's exit-0
    ``terminated`` beside it changes nothing. A preempted head below
    ``n`` is driven once, and a drive that completes short of ``n`` is
    ``Unreachable`` at its exit."""
    ch = channel(
        [
            (1, claim()),
            (2, value("loss", 0.3)),
            (3, commit(1, None, [2])),
            (4, stopped(1, 3, [], completed=True)),
            (5, exited("L1", 0)),  # its launch's reap: a fact about a process
        ]
    )
    p = Fake(ch)
    assert ensure(p, LOSS3, sleep=never) == Unreachable(4)
    assert p.extends == []

    ch = channel([(1, claim()), (2, value("loss", 0.3)), (3, commit(1, None, [2]))])
    at(ch, stopped(1, 3, []))

    def completes_short(ch, target):
        c = at(ch, claim(launch="L1", resume_from=4))
        at(
            ch,
            value("loss", 0.2),
            commit(c, 4, [c + 1]),
            stopped(c, c + 2, [], completed=True),
        )
        return Handle("L1")

    p = Fake(ch, completes_short)
    assert ensure(p, LOSS3, sleep=never) == Unreachable(ch.last_seq())
    assert len(p.extends) == 1


def test_ensure_of_zero_on_the_empty_lineage_is_reached_at_none():
    """A target of 0 is reached at once, and on the empty lineage at ``None``, the
    one ``Reached`` with nothing to read (§5)."""
    p = Fake(MemoryChannel())
    assert ensure(p, Target("loss", 0), sleep=never) == Reached(None)
    assert ensure(p, Target(Commits(), 0), sleep=never) == Reached(None)
    assert p.extends == []


# ----- the produce loop ---------------------------------------------------------------


def test_ensure_follows_the_newest_claim():
    """The produce loop polls the newest claim's head, never the drive's own: here
    the drive's claim B reaches three ``loss`` elements and exits, and a newer claim
    C restarts from the root. ``ensure`` reads C's lineage, not B's, re-drives once
    B has ended (onto C, which the producer hands back as foreign), and returns
    ``Reached`` once C's lineage holds the target."""
    ch = MemoryChannel()

    def b_then_c(ch, target):
        b = at(ch, claim(launch="L1"))
        at(
            ch,
            value("loss", 0.3),
            commit(b, None, [b + 1]),
            value("loss", 0.2),
            commit(b, b + 2, [b + 3]),
            value("loss", 0.1),
            commit(b, b + 4, [b + 5]),
            stopped(b, b + 6, []),
        )
        c = at(ch, claim(launch="L0"))
        at(ch, value("loss", 0.9), commit(c, None, [c + 1]))
        return Handle("L1")

    def onto_c(ch, target):
        return foreign_episode(ch)

    def c_delivers():
        c = 9
        at(
            ch,
            value("loss", 0.8),
            commit(c, 11, [12]),
            value("loss", 0.7),
            commit(c, 13, [14]),
        )

    p = Fake(ch, b_then_c, onto_c)
    assert ensure(p, LOSS3, sleep=after(2, c_delivers)) == Reached(15)
    assert prefix_len(ch, "loss", head=AtNode(8)) == 3  # B's lineage, not read
    assert len(p.extends) == 2


def test_ensure_redrives_when_a_foreign_claim_ends_below_the_target():
    """A foreign claim held when ``ensure`` drives is waited on (the producer hands
    back ``foreign_episode``); once it ends below the target, ``ensure`` drives
    again, and a foreign drive that wrote nothing is never a no-progress error."""
    ch = channel(
        [(1, claim(launch="L0")), (2, value("loss", 0.3)), (3, commit(1, None, [2]))]
    )

    def wait_on_it(ch, target):
        return foreign_episode(ch)

    def resumes(ch, target):
        c = at(ch, claim(launch="L1", resume_from=4))
        at(
            ch,
            value("loss", 0.2),
            commit(c, 4, [c + 1]),
            value("loss", 0.1),
            commit(c, c + 2, [c + 3]),
        )
        return Handle("L1", alive=True)

    p = Fake(ch, wait_on_it, resumes)
    sleep = after(1, lambda: at(ch, stopped(1, 3, [])))
    assert ensure(p, LOSS3, sleep=sleep) == Reached(ch.last_seq())
    assert len(p.extends) == 2


def test_ensure_tests_the_outcomes_before_the_failure_exits():
    """``Reached`` and ``Unreachable`` are tested before the failure exits (§5), so
    a drive that resumes from a node already holding the target is ``Reached``
    though it errors having written no commit, and one that completes having
    written none is ``Unreachable``, not a no-progress error."""
    # A's lineage reaches 3 at seq 7, but A rewinds to seq 3 and exits there.
    rows = [
        (1, claim()),
        (2, value("loss", 0.3)),
        (3, commit(1, None, [2])),
        (4, value("loss", 0.2)),
        (5, commit(1, 3, [4])),
        (6, value("loss", 0.1)),
        (7, commit(1, 5, [6])),
    ]
    rewound = [(8, rewind(1, 3)), (9, stopped(1, 3, []))]
    ch = channel(rows + rewound)
    assert prefix_len(ch, "loss", head=LatestClaimHead()) == 1

    def errors_from_7(ch, target):
        c = at(ch, claim(launch="L1", resume_from=7))
        at(ch, stopped(c, 7, None, error="boom"))
        return Handle("L1")

    assert ensure(Fake(ch, errors_from_7), LOSS3, sleep=never) == Reached(11)

    ch = channel(rows + rewound)

    def completes_at_once(ch, target):
        c = at(ch, claim(launch="L1", resume_from=9))
        at(ch, stopped(c, 9, [], completed=True))
        return Handle("L1")

    assert ensure(Fake(ch, completes_at_once), LOSS3, sleep=never) == Unreachable(11)


def test_ensure_raises_run_failed_when_its_drives_claim_fails():
    """A drive's claim that ends with an outcome in ``Outcome.failures()`` raises
    ``RunFailedError``: its own errored exit, or a death report. The error carries
    the verdict at raise time, stamped with the run, the target and the count."""
    ch = MemoryChannel()

    def errors(ch, target):
        c = at(ch, claim(launch="L1"))
        at(
            ch,
            value("loss", 0.3),
            commit(c, None, [c + 1]),
            stopped(c, c + 2, None, error="boom"),
        )
        return Handle("L1")

    with pytest.raises(RunFailedError, match="boom|errored") as ei:
        ensure(Fake(ch, errors, run_id="r"), LOSS3, sleep=never)
    e = ei.value
    assert e.run_id == "r" and e.target == LOSS3 and e.count == 1
    assert e.result.outcomes == {Outcome.ERRORED} and e.result.tier == Tier.EXIT
    assert e.result.run_id == "r" and e.result.error == "boom"

    ch = MemoryChannel()

    def killed(ch, target):
        c = at(ch, claim(launch="L1"))
        at(ch, died(c, "killed", evidence="slurm:TIMEOUT"))
        return Handle("L1")

    with pytest.raises(RunFailedError) as ei:
        ensure(Fake(ch, killed), LOSS3, sleep=never)
    assert ei.value.result.outcomes == {Outcome.KILLED}
    assert ei.value.result.tier == Tier.DIED and ei.value.count == 0


def test_ensure_raises_no_progress_when_its_drives_claim_ends_without_a_commit():
    """A drive's claim that ends, not failing, having written no commit raises
    ``NoProgressError``: a re-drive would spin."""
    ch = channel(
        [
            (1, claim()),
            (2, value("loss", 0.3)),
            (3, commit(1, None, [2])),
            (4, stopped(1, 3, [])),
        ]
    )

    def stuck(ch, target):
        c = at(ch, claim(launch="L1", resume_from=4))
        at(ch, stopped(c, 4, []))
        return Handle("L1")

    with pytest.raises(NoProgressError, match="no commit") as ei:
        ensure(Fake(ch, stuck, run_id="r"), LOSS3, sleep=never)
    assert (ei.value.run_id, ei.value.target, ei.value.count) == ("r", LOSS3, 1)


def test_no_progress_is_measured_by_commits_per_drive():
    """Progress is a drive's claim's commits, not ``x``'s count (§5): a chunk that
    commits with no new element of a sparse stream, and a claim that resumes from an
    older checkpoint, which lowers every count, each re-drive with no error."""
    ch = channel(
        [
            (1, claim()),
            (2, value("loss", 0.3)),
            (3, commit(1, None, [2])),
            (4, stopped(1, 3, [])),
        ]
    )

    def sparse(ch, target):
        c = at(ch, claim(launch="L1", resume_from=4))
        at(ch, commit(c, 4, []), stopped(c, c + 1, []))  # no new loss
        return Handle("L1")

    def older(ch, target):
        c = at(ch, claim(launch="L2", resume_from=3))  # behind the head
        at(ch, commit(c, 3, []), stopped(c, c + 1, []))
        return Handle("L2")

    def reaches(ch, target):
        c = at(ch, claim(launch="L3", resume_from=ch.last_seq()))
        at(
            ch,
            value("loss", 0.2),
            commit(c, c - 1, [c + 1]),
            value("loss", 0.1),
            commit(c, c + 2, [c + 3]),
        )
        return Handle("L3", alive=True)

    p = Fake(ch, sparse, older, reaches)
    paced = Paced(p)
    assert ensure(p, LOSS3, sleep=paced) == Reached(ch.last_seq())
    assert len(p.extends) == 3
    assert paced.waits == [(0.01, 1), (0.01, 2)]  # one before each re-drive


def test_ensure_raises_recordless_exit_at_the_fixed_point():
    """A drive whose handle ended with no claim naming its launch, and no other
    claim held, is recordless; the first re-drives, and the second at an unchanged
    head and ``Commits()`` count raises ``RecordlessExitError`` (plan, open question
    15): a re-drive reproduced it. A recordless drive followed by one that delivers
    succeeds: the bound is the fixed point, not a count. An unclaimed launch's exit-0
    ``terminated``, a fact about a process, short-circuits nothing."""
    ch = channel(
        [
            (1, claim()),
            (2, value("loss", 0.3)),
            (3, commit(1, None, [2])),
            (4, stopped(1, 3, [])),
        ]
    )
    at(ch, exited("L0", 0))  # e.g. an sbatch that exits 0 at submit time

    def recordless(launch):
        def drive(ch, target):
            h = Handle(launch)
            return h

        return drive

    p = Fake(ch, recordless("L1"), recordless("L2"), run_id="r")
    paced = Paced(p)
    with pytest.raises(RecordlessExitError) as ei:
        ensure(p, LOSS3, sleep=paced)
    e = ei.value
    assert (e.run_id, e.launch_id, e.target, e.count) == ("r", "L2", LOSS3, 1)
    assert len(p.extends) == 2
    assert paced.waits == [(0.01, 1)]  # the re-drive's

    ch = channel(
        [
            (1, claim()),
            (2, value("loss", 0.3)),
            (3, commit(1, None, [2])),
            (4, stopped(1, 3, [])),
        ]
    )

    def delivers(ch, target):
        c = at(ch, claim(launch="L2", resume_from=4))
        at(
            ch,
            value("loss", 0.2),
            commit(c, 4, [c + 1]),
            value("loss", 0.1),
            commit(c, c + 2, [c + 3]),
        )
        return Handle("L2")

    p = Fake(ch, recordless("L1"), delivers)
    paced = Paced(p)
    assert ensure(p, LOSS3, sleep=paced) == Reached(ch.last_seq())
    assert paced.waits == [(0.01, 1)]


def test_a_foreign_drive_that_drove_nothing_is_bounded_and_paced():
    """A foreign handle (no launch) that has ended, with no claim naming it and none
    held, counts toward ``RecordlessExitError``'s fixed point (§5, amended
    2026-10-30), its ``launch_id`` then ``None``: a producer handing back
    ``foreign_episode`` over an ended claim cannot make ``ensure`` re-drive without
    bound. Every re-drive waits ``poll_interval`` first."""
    ch = channel([(1, claim()), (2, commit(1, None, [])), (3, stopped(1, 2, []))])
    calls = []

    def over_an_ended_claim(ch, target):
        calls.append(len(slept))
        return foreign_episode(ch)

    slept = []
    p = Fake(ch, *[over_an_ended_claim] * 50, run_id="r")
    target = Target(Commits(), 5)
    with pytest.raises(RecordlessExitError) as ei:
        ensure(p, target, sleep=slept.append, poll_interval=0.25)
    e = ei.value
    assert (e.run_id, e.launch_id, e.target, e.count) == ("r", None, target, 1)
    assert calls == [0, 1]  # two drives, the second after one paced wait
    assert slept == [0.25]


def test_a_drive_whose_handle_ended_while_its_claim_runs_waits_and_reports_nothing():
    """While a drive's claim is held, ``ensure`` waits, whatever the drive's handle
    says (§5): an ended handle may be a wrapper's, whose grandchild claimed, and the
    claimant's launcher reports its death (§3). ``ensure`` reaps the handle once,
    and writes no death report on its strength."""
    ch = MemoryChannel()
    h = Handle("L1")

    def wrapper(ch, target):
        at(ch, claim(launch="L1"))  # the grandchild's claim, alive
        return h

    def grandchild_delivers():
        at(
            ch,
            value("loss", 0.3),
            commit(1, None, [2]),
            value("loss", 0.2),
            commit(1, 3, [4]),
            value("loss", 0.1),
            commit(1, 5, [6]),
        )

    assert ensure(
        Fake(ch, wrapper), LOSS3, sleep=after(5, grandchild_delivers)
    ) == Reached(7)
    assert ch.read(topics=[Topic.SUPERVISOR_DIED]) == []
    assert h.waited == 1


def test_a_claim_race_loser_raises_nothing_while_the_winner_runs():
    """A drive that lost the claim race ends with no claim naming its launch while
    the winner's claim is held: ``ensure`` raises nothing and waits on the winner
    (§5: "never while another claim is unended")."""
    ch = MemoryChannel()

    def loses(ch, target):
        at(ch, claim(launch="L0"))  # the winner, another dispatcher's
        return Handle("L1")  # ours lost: it exits having written nothing

    def winner_delivers():
        at(
            ch,
            value("loss", 0.3),
            commit(1, None, [2]),
            value("loss", 0.2),
            commit(1, 3, [4]),
            value("loss", 0.1),
            commit(1, 5, [6]),
        )

    p = Fake(ch, loses)
    assert ensure(p, LOSS3, sleep=after(3, winner_delivers)) == Reached(7)
    assert len(p.extends) == 1


def test_ensure_reports_on_each_poll_for_its_own_drive_and_a_foreign_one():
    """``ensure`` calls ``report_if_dead`` on each poll (§3, "The writers"), whoever's
    the held claim: its own drive's, whose handle says alive, is reported ``lost``
    and so fails the drive; a foreign one, dead since it was handed back
    (``foreign_episode``), is reported, and ``ensure`` drives again."""
    ch = MemoryChannel()
    gone = dead_handle()

    def dead_claimant(ch, target):
        at(ch, claim(launch="L1", handle=gone))
        return Handle("L1", alive=True)  # e.g. a wrapper that outlives it

    with pytest.raises(RunFailedError) as ei:
        ensure(Fake(ch, dead_claimant), LOSS3, sleep=never)
    assert ei.value.result.outcomes == {Outcome.LOST}
    assert [r.evidence for r in ei.value.result.reports] == [f"probe: {gone}"]

    ch = channel(
        [(1, claim(launch="L0")), (2, value("loss", 0.3)), (3, commit(1, None, [2]))]
    )

    def wait_on_it(ch, target):
        return foreign_episode(ch)

    def recovers(ch, target):
        c = at(ch, claim(launch="L1", resume_from=3))
        at(
            ch,
            value("loss", 0.2),
            commit(c, 3, [c + 1]),
            value("loss", 0.1),
            commit(c, c + 2, [c + 3]),
        )
        return Handle("L1", alive=True)

    def dies():  # the foreign claim's process dies with no exit
        ch.send(
            {**claim(handle=gone, resume_from=3).body},
            topic=Topic.LIFECYCLE_STARTED,
            request_id="L9",
        )

    p = Fake(ch, wait_on_it, recovers)
    assert ensure(p, LOSS3, sleep=after(1, dies)) == Reached(ch.last_seq())
    reports = ch.read(topics=[Topic.SUPERVISOR_DIED])
    assert [(e.body["claim_seq"], e.body["evidence"]) for e in reports] == [
        (4, f"probe: {gone}")
    ]


def test_ensure_refuses_a_target_its_drives_claim_declares_a_source():
    """A target the drive's claim declares a source counts 0 forever, so each chunk
    would commit without reaching it: ``ValueError`` once that claim is on the log
    (Review Focus 2; plan, open question 1)."""
    ch = MemoryChannel()

    def as_source(ch, target):
        c = at(ch, claim(launch="L1", sources=["loss"]))
        at(ch, value("loss", 0.3), commit(c, None, [c + 1]))
        return Handle("L1", alive=True)

    with pytest.raises(ValueError, match="source"):
        ensure(Fake(ch, as_source), LOSS3, sleep=never)


def test_a_drives_claim_is_the_newest_naming_its_launch():
    """A drive's claim is the newest claim naming its launch (§5): an older claim
    of the same launch, ended, is not the drive's."""
    ch = MemoryChannel()

    def twice(ch, target):
        a = at(ch, claim(launch="L1"))
        at(ch, stopped(a, None, None, error="first try"))  # would fail the drive
        b = at(ch, claim(launch="L1"))
        at(
            ch,
            value("loss", 0.3),
            commit(b, None, [b + 1]),
            value("loss", 0.2),
            commit(b, b + 2, [b + 3]),
        )
        return Handle("L1", alive=True)

    def delivers():
        at(ch, value("loss", 0.1), commit(3, 7, [8]))

    assert ensure(Fake(ch, twice), LOSS3, sleep=after(1, delivers)) == Reached(9)


# ----- foreign_episode ----------------------------------------------------------------


def test_foreign_episode_is_alive_is_a_pure_read_of_ended():
    """``foreign_episode``'s ``is_alive()`` is "the latest claim has not ended", by
    presence (§3, "The writers"): no probe, and nothing written, so a dead process
    nobody reported reads alive, and an unreadable exit naming the claim ends it.
    It names no launch, and ``wait()`` reaps nothing."""
    ch = MemoryChannel()
    h = foreign_episode(ch)
    assert h.launch_id is None and h.wait() is None
    assert h.is_alive() is False  # nothing claimed
    at(ch, claim(handle=dead_handle()))
    assert h.is_alive() is True and ch.last_seq() == 1  # no probe, no report
    at(ch, junk(stopped(1, None, []), honored="nope"))
    assert h.is_alive() is False  # an unreadable exit ends its claim
    at(ch, claim())
    assert h.is_alive() is True
    at(ch, died(3, "killed"))
    assert h.is_alive() is False


# ----- launch_producer and the staged Worker -----------------------------------------


def loss_worker(ch, *, until):
    """A stateless resumable worker: it continues the head, emits ``loss`` at each
    iteration, and exits preempted at its target."""
    with Worker(ch, resume_from=LatestClaimHead(), sources=[], save=NoSave()) as w:
        for i in w.steps(until):
            w.emit("loss", float(i))


def producer(launcher, run_id="exp", fn=loss_worker, **kwargs):
    return launch_producer(launcher, run_id, thread(fn, **kwargs))


def losses(ch):
    return [e.value for e in series(ch, "loss", head=LatestClaimHead())]


def test_launch_producer_drives_its_target_with_until():
    """``launch_producer(launcher, run_id, target)``'s ``extend(t)`` launches
    ``target.driven(t)``: the worker receives the drive's ``Target`` as ``until`` (plan, open
    question 18), and the handle carries the launch its claim names."""
    launcher = ThreadLauncher()
    seen = {}

    def fn(ch, *, tag, until):
        seen["until"], seen["tag"] = until, tag

    p = producer(launcher, fn=fn, tag="x")
    assert p.run_id == "exp"
    h = p.extend(LOSS3)
    h.wait()
    assert seen == {"until": LOSS3, "tag": "x"}
    ch = p.channel
    launched = ch.read(topics=[Topic.SUPERVISOR_LAUNCHED])
    assert [e.request_id for e in launched] == [h.launch_id]


def test_launch_producer_hands_back_a_foreign_episode_while_a_claim_is_held():
    """While the run's newest claim is held, ``extend`` launches nothing and hands
    back ``foreign_episode`` (``relaunch_if_needed`` returned ``None``)."""
    launcher = ThreadLauncher()
    at(launcher.create_channel("exp"), claim())
    p = producer(launcher)
    h = p.extend(LOSS3)
    assert h.launch_id is None and h.is_alive() is True
    assert p.channel.read(topics=[Topic.SUPERVISOR_LAUNCHED]) == []


def test_ensure_cold_miss_then_hit():
    """A miss drives the worker to the target, and a second call is a read: no
    second launch."""
    launcher = ThreadLauncher()
    p = producer(launcher)
    got = ensure(p, Target("loss", 5), sleep=bounded_sleep())
    assert isinstance(got, Reached)
    ch = p.channel
    assert prefix_len(ch, "loss", head=AtNode(got.node)) == 5
    exited_by(ch)
    again = ensure(p, Target("loss", 5), sleep=never)
    assert isinstance(again, Reached)
    assert prefix_len(ch, "loss", head=AtNode(again.node)) == 5
    assert len(ch.read(topics=[Topic.SUPERVISOR_LAUNCHED])) == 1


def exited_by(ch, *, timeout=30.0):
    """Wait until the run's worker has exited (its ``stopped`` is on the log)."""
    deadline = time.monotonic() + timeout
    while ch.latest(Topic.LIFECYCLE_STOPPED) is None:
        if time.monotonic() > deadline:
            raise AssertionError("the worker never exited")
        time.sleep(0.01)


def test_a_larger_target_continues_from_the_earlier_drives_extendable_exit():
    """The worker exits preempted at its target, an extendable exit, so a later
    ``ensure`` with a larger target continues from it (§5, "The producer seam"):
    one series, and the second claim resumes from the first claim's exit."""
    launcher = ThreadLauncher()
    p = producer(launcher)
    assert isinstance(ensure(p, LOSS3, sleep=bounded_sleep()), Reached)
    got = ensure(p, Target("loss", 6), sleep=bounded_sleep())
    assert isinstance(got, Reached)
    ch = p.channel
    assert [e.value for e in series(ch, "loss", head=AtNode(got.node))] == [
        0.0,
        1.0,
        2.0,
        3.0,
        4.0,
        5.0,
    ]
    claims = ch.read(topics=[Topic.LIFECYCLE_STARTED])
    first_exit = ch.read(topics=[Topic.LIFECYCLE_STOPPED])[0]
    assert first_exit.body["claim_seq"] == claims[0].seq
    assert claims[-1].body["resume_from"] == first_exit.seq


def test_ensure_redrives_within_one_call_to_reach_the_target():
    """A worker that commits at most two elements a claim is driven again and
    again by one call, continuing the head each time, until the target is
    reached."""
    launcher = ThreadLauncher()

    def chunked(ch, *, until):
        done = prefix_len(ch, until.x, head=LatestClaimHead())
        chunk = Target(until.x, min(until.n, done + 2))
        with Worker(ch, resume_from=LatestClaimHead(), sources=[], save=NoSave()) as w:
            for i in w.steps(chunk):
                w.emit("loss", float(i))

    p = producer(launcher, fn=chunked)
    got = ensure(p, Target("loss", 7), sleep=bounded_sleep())
    assert isinstance(got, Reached)
    ch = p.channel
    assert losses(ch) == [float(i) for i in range(7)]
    assert len(ch.read(topics=[Topic.SUPERVISOR_LAUNCHED])) == 4


def test_ensure_fails_fast_on_a_launched_workers_raise_and_a_re_call_resumes():
    """A drive whose worker raises fails the call with the verdict (the retry is the
    caller's); a re-call resumes from the head and reaches the target."""
    launcher = ThreadLauncher()
    tries = []

    def flaky(ch, *, until):
        tries.append(1)
        with Worker(ch, resume_from=LatestClaimHead(), sources=[], save=NoSave()) as w:
            for i in w.steps(until):
                w.emit("loss", float(i))
                if len(tries) == 1 and i == 1:
                    raise RuntimeError("boom")

    p = producer(launcher, fn=flaky)
    with pytest.raises(RunFailedError) as ei:
        ensure(p, Target("loss", 4), sleep=bounded_sleep())
    assert ei.value.result.outcomes == {Outcome.ERRORED}
    assert ei.value.run_id == "exp"
    got = ensure(p, Target("loss", 4), sleep=bounded_sleep())
    assert isinstance(got, Reached)
    # the errored exit tore its open iteration: loss 1 was never committed
    assert losses(p.channel) == [0.0, 1.0, 2.0, 3.0]


def test_ensure_raises_no_progress_for_a_launched_worker_that_commits_nothing():
    """A worker that claims and exits without a commit, whatever its target, makes
    a re-drive spin: ``NoProgressError``."""
    launcher = ThreadLauncher()

    def idle(ch, *, until):
        with Worker(ch, resume_from=LatestClaimHead(), sources=[], save=NoSave()):
            pass

    with pytest.raises(NoProgressError):
        ensure(producer(launcher, fn=idle), LOSS3, sleep=bounded_sleep())


def test_a_worker_that_dies_before_it_claims_is_a_recordless_exit():
    """A worker that raises before it constructs its ``Worker`` leaves no claim, only
    its launch's ``terminated``: recordless, and the re-drive reproduces it, so the
    second raises ``RecordlessExitError`` rather than hang."""
    launcher = ThreadLauncher()

    def dies(ch, *, until):
        raise RuntimeError("died before claiming")

    p = producer(launcher, fn=dies)
    with pytest.raises(RecordlessExitError):
        ensure(p, LOSS3, sleep=bounded_sleep())
    assert len(p.channel.read(topics=[Topic.SUPERVISOR_LAUNCHED])) == 2


def test_a_completing_worker_is_a_function_producer():
    """specs/derived-runs.md's dissolution pin, in 0.4.0 terms: compute-on-demand
    needs no new surface. A worker that emits its bundle and completes, its exit
    committing it, is reached at that closed exit, and a second call is a read."""
    launcher = ThreadLauncher()

    def derived(ch, *, until):
        with Worker(ch, resume_from=None, sources=[], save=NoSave()) as w:
            w.emit("pair_metrics", 0.42)
            w.emit("hubness", 7.0)
            w.stopped(completed=True, error=None)

    p = producer(launcher, run_id="analysis-abc123", fn=derived)
    got = ensure(p, Target("pair_metrics", 1), sleep=bounded_sleep())
    assert isinstance(got, Reached)
    ch = p.channel
    assert [e.value for e in series(ch, "pair_metrics", head=AtNode(got.node))] == [
        0.42
    ]
    assert ensure(p, Target("pair_metrics", 1), sleep=never) == got
    assert len(ch.read(topics=[Topic.LIFECYCLE_STARTED])) == 1
    assert streams(ch, head=LatestClaimHead()).eager == {"pair_metrics", "hubness"}


def test_reuse_is_extend_across_drivers(tmp_path):
    """specs/store.md pin 1: two drivers demanding one rid converge on one log: A
    computes a prefix, B extends the same log, and the series is continuous."""
    rid = "abc123"
    home = tmp_path / "runs" / rid[:2] / rid
    home.mkdir(parents=True)
    a = producer(ThreadLauncher(root=home, backend="sqlite"), run_id=rid)
    b = producer(ThreadLauncher(root=home, backend="sqlite"), run_id=rid)
    assert isinstance(ensure(a, LOSS3, sleep=bounded_sleep()), Reached)
    assert isinstance(ensure(b, Target("loss", 8), sleep=bounded_sleep()), Reached)
    assert losses(b.channel) == [float(i) for i in range(8)]
    assert sorted((tmp_path / "runs").rglob("*.db")) == [
        FORMATS[LOG_FORMAT].sqlite_path(home, rid)
    ]
    assert len(b.channel.read(topics=[Topic.LIFECYCLE_STARTED])) == 2


def test_a_latecomer_waits_on_a_live_foreign_claim_and_launches_nothing():
    """specs/store.md pin 2a: while a foreign claim is held, a latecomer's ``ensure``
    waits on it, launching nothing, and is reached once the winner delivers."""
    launcher = ThreadLauncher()
    ch = launcher.create_channel("exp")
    at(ch, claim())
    p = producer(launcher)
    delivered = []

    def winner(_):
        if len(delivered) < 3:
            parent = None if not delivered else delivered[-1]
            v = at(ch, value("loss", 1.0))
            delivered.append(at(ch, commit(1, parent, [v])))

    assert ensure(p, LOSS3, sleep=winner) == Reached(delivered[-1])
    assert ch.read(topics=[Topic.SUPERVISOR_LAUNCHED]) == []


# ----- history ------------------------------------------------------------------------


def epoch_log():
    """A claim whose epoch is t=100, then five commits at t=100..104, each
    committing one ``loss`` and one ``acc``."""
    rows = [(1, claim(t=100.0))]
    parent = None
    seq = 2
    for i in range(5):
        rows += [
            (seq, value("loss", float(i), t=100.0 + i)),
            (seq + 1, value("acc", -1.0, t=100.0 + i)),
            (seq + 2, commit(1, parent, [seq, seq + 1], t=100.0 + i)),
        ]
        parent = seq + 2
        seq += 3
    return channel(rows)


def loss_values(elements):
    return [e.value for e in elements]


def test_history_evaluates_once_per_node():
    """``history`` replays a schedule over the lineage's nodes with
    ``Subscription.evaluate``, once per node, as live evaluation does (§5): the call
    is the firing, so a ``count`` until ends after its nth node, not its nth call.
    Time atoms read each node's ``t`` from the epoch, the earliest ``started.t``. It
    returns the elements of the stream committed at the nodes where it fires, by
    name, every one of them, with no collapse."""
    ch = epoch_log()
    h = LatestClaimHead()
    every = history(ch, "loss", {"every": {"commits": 2}}, head=h)
    assert loss_values(every) == [0.0, 2.0, 4.0]
    assert all(isinstance(e, Element) for e in every)
    assert loss_values(
        history(ch, "loss", {"every": {"commits": 1}, "until": {"count": 2}}, head=h)
    ) == [0.0, 1.0]
    # time: every 2 s of the nodes' t, from the epoch, until 3 s after it
    assert loss_values(history(ch, "loss", {"every": {"time_seconds": 2}}, head=h)) == [
        0.0,
        2.0,
        4.0,
    ]
    assert loss_values(
        history(
            ch, "loss", {"every": {"commits": 1}, "until": {"time_seconds": 3}}, head=h
        )
    ) == [0.0, 1.0, 2.0]
    assert history(ch, "loss", {"from": {"stream": "loss", "n": 9}}, head=h) == []
    # one-shot: no every, fires once
    assert loss_values(history(ch, "loss", {"from": {"commits": 3}}, head=h)) == [2.0]
    # by name, and a node with two elements of it yields both
    assert loss_values(history(ch, "acc", {"every": {"commits": 4}}, head=h)) == [
        -1.0,
        -1.0,
    ]
    two = channel(
        [
            (1, claim()),
            (2, value("loss", 0.1)),
            (3, value("loss", 0.2)),
            (4, commit(1, None, [2, 3])),
        ]
    )
    assert loss_values(history(two, "loss", {"every": {"commits": 1}}, head=h)) == [
        0.1,
        0.2,
    ]
    # at another node, its lineage
    assert loss_values(
        history(ch, "loss", {"every": {"commits": 1}}, head=AtNode(7))
    ) == [0.0, 1.0]
    assert history(MemoryChannel(), "loss", {"every": {"commits": 1}}, head=h) == []


def test_history_parses_its_schedule_at_its_boundary():
    """``history`` parses its wire schedule (``parse_schedule``): a malformed one
    raises ``MalformedSchedule`` to its caller, as the 0.3.0 ``step`` atom does."""
    ch = epoch_log()
    with pytest.raises(MalformedSchedule):
        history(ch, "loss", {"every": {"step": 1}}, head=LatestClaimHead())
    with pytest.raises(MalformedSchedule):
        history(ch, "loss", {"sometimes": {}}, head=LatestClaimHead())


def test_history_reads_the_epoch_only_for_a_time_atom_in_from_or_until():
    """The epoch, the earliest ``started.t``, is read only for a schedule with an
    ``Elapsed`` atom in ``from`` or ``until`` (``needs_epoch``), the slots that count
    from registration: an unreadable first claim from a dead past poisons no
    count-only read and no ``every`` time atom, which counts from its last firing,
    and each still returns its elements; a time atom in ``until``, which needs that
    ``t``, raises ``MalformedRecordError``."""
    ch = channel(
        [
            (1, junk(claim(), t="junk")),  # a dead past's claim, unreadable
            (2, claim()),
            (3, value("loss", 0.3)),
            (4, commit(2, None, [3])),
            (5, value("loss", 0.2)),
            (6, commit(2, 4, [5])),
        ]
    )
    h = LatestClaimHead()
    assert loss_values(history(ch, "loss", {"every": {"commits": 1}}, head=h)) == [
        0.3,
        0.2,
    ]
    every_second = {"every": {"time_seconds": 1}}
    assert loss_values(history(ch, "loss", every_second, head=h)) == [0.3]
    with pytest.raises(MalformedRecordError):
        history(ch, "loss", {"until": {"time_seconds": 1}}, head=h)
    with pytest.raises(MalformedRecordError):
        history(ch, "loss", {"from": {"time_seconds": 1}}, head=h)
