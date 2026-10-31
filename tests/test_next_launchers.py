"""The reference launchers' 0.3.0 tests, ported to the staged launchers and the staged
Worker (docs/specs/commits-and-lineage.md §3, supervisor-v0.5, "The writers"; §5;
plan Task 15, from ``tests/test_thread_launcher.py`` and
``tests/test_local_launcher.py``).

A launch now takes its launcher's target record (plan, open question 18):
``launch(rid, fn, args=, kwargs=)`` is ``launch(rid, ThreadTarget(fn, args=,
kwargs=))``, and ``launch(rid, cmd, env=)`` is ``launch(rid, Command(argv, env=))``.
The bracket is ``supervisor.*``. The death reports the launchers write are
``tests/test_next_death_reports.py``'s; these pin the bracket, the handle, and the
verdicts a launch leaves.
"""

import sys
import threading

import pytest

from runstate._next.checkpoint import NoSave
from runstate._next.launcher import (
    Command,
    LocalLauncher,
    ThreadLauncher,
    ThreadTarget,
)
from runstate._next.observables import Tier, peek_terminal
from runstate._next.vocabulary.coordinate import Commits, Target
from runstate._next.vocabulary.payloads import Outcome, Topic
from runstate._next.worker import Worker

pytestmark = pytest.mark.filterwarnings(
    "error::pytest.PytestUnhandledThreadExceptionWarning"
)


def thread(fn, *args, **kwargs):
    return ThreadTarget(fn, args=args, kwargs=kwargs)


# ----- ThreadLauncher ---------------------------------------------------------------


def test_launch_runs_target_and_brackets_with_supervisor_records(tmp_path):
    """``supervisor.launched`` brackets the work, and ``supervisor.terminated`` closes
    it, both naming the launch."""
    launcher = ThreadLauncher(root=tmp_path)
    seen = []

    def target(ch):
        seen.append(ch)
        ch.send({"value": 1, "t": None}, topic=Topic.VALUE, name="loss")

    h = launcher.launch("run-1", thread(target))
    h.wait()
    assert len(seen) == 1
    ch = launcher.attach_channel("run-1")
    records = ch.read()
    assert [e.topic for e in records] == [
        Topic.SUPERVISOR_LAUNCHED,
        Topic.VALUE,
        Topic.SUPERVISOR_TERMINATED,
    ]
    assert {records[0].request_id, records[-1].request_id} == {h.launch_id}
    launched, term = records[0].body, records[-1].body
    assert launched["handle"].startswith("local://") and launched["status"] == "running"
    assert (term["reason"], term["exit_code"]) == ("exited", 0)


def test_handle_fields_and_liveness(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    gate = threading.Event()
    h = launcher.launch("run-2", thread(lambda ch: gate.wait()))
    assert h.run_id == "run-2" and h.handle.startswith("local://")
    assert h.is_alive() is True
    gate.set()
    h.wait()
    assert h.is_alive() is False


def test_an_errored_target_that_never_claimed_reads_errored(tmp_path):
    """A target that raises before it claims records exit code 1, and the unclaimed
    run's verdict is its launch's ``terminated``: ``errored`` (§5)."""
    launcher = ThreadLauncher(root=tmp_path)

    def target(ch):
        raise RuntimeError("boom")

    h = launcher.launch("run-3", thread(target))
    h.wait()
    assert isinstance(h.exception, RuntimeError)
    ch = launcher.attach_channel("run-3")
    term = ch.latest(Topic.SUPERVISOR_TERMINATED).body
    assert (term["reason"], term["exit_code"]) == ("exited", 1)
    r = peek_terminal(ch)
    assert (r.outcomes, r.tier) == ({Outcome.ERRORED}, Tier.TERMINATED)


def test_target_receives_args(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    got = []

    def target(ch, a, b, *, c):
        got.append((a, b, c))

    launcher.launch("run-4", thread(target, 1, 2, c=3)).wait()
    assert got == [(1, 2, 3)]


def test_a_claim_losers_clean_exit_does_not_forge_the_winners_verdict(tmp_path):
    """Two dispatchers, one run: the loser's launch is the newest, and its clean exit
    the newest ``terminated``, while the winner is still alive. The winner is slow to
    claim, so its claim lands after the loser's launch: the launch id, not the
    position and not the handle (threads share one pid), attributes the claim
    (specs/launcher-record-identity.md). The loser's end writes ``terminated`` only,
    since no claim names its launch, and ends nothing."""
    launcher = ThreadLauncher(root=tmp_path)
    claim_now, winner_claimed = threading.Event(), threading.Event()
    loser_done, release_winner = threading.Event(), threading.Event()

    def winner(ch):
        claim_now.wait(20)
        with Worker(ch, resume_from=None, sources=[], save=NoSave()) as w:
            winner_claimed.set()
            release_winner.wait(20)
            for _ in w.steps(Target(Commits(), 1)):
                pass

    def loser(ch):
        winner_claimed.wait(20)
        with Worker(ch, resume_from=None, sources=[], save=NoSave()) as w:
            assert not w.claimed
            for _ in w.steps(Target(Commits(), 3)):
                pass
        loser_done.set()

    hw = launcher.launch("collide", thread(winner))
    hl = launcher.launch("collide", thread(loser))
    claim_now.set()
    assert loser_done.wait(20) and hl.wait() is None
    ch = launcher.attach_channel("collide")
    claims = ch.read(topics=[Topic.LIFECYCLE_STARTED])
    assert [c.request_id for c in claims] == [hw.launch_id]
    assert claims[0].seq > ch.read(topics=[Topic.SUPERVISOR_LAUNCHED])[-1].seq
    terms = ch.read(topics=[Topic.SUPERVISOR_TERMINATED])
    assert [t.request_id for t in terms] == [hl.launch_id]
    assert ch.read(topics=[Topic.SUPERVISOR_DIED]) == []
    assert peek_terminal(ch) is None  # the winner runs on

    release_winner.set()
    hw.wait()
    r = peek_terminal(ch)
    assert (r.outcomes, r.tier) == ({Outcome.PREEMPTED}, Tier.EXIT)


# ----- LocalLauncher ----------------------------------------------------------------

# A worker that opens its run's channel from the environment and records a value.
WORKER = (
    "import runstate;"
    "ch = runstate.current_channel();"
    "ch.send({'value': 0.5, 't': None}, topic='value', name='loss')"
)

# The staged Worker in a child: it claims, commits twice, and completes.
STAGED = """
from runstate import current_channel
from runstate._next.checkpoint import NoSave
from runstate._next.vocabulary.coordinate import Commits, Target
from runstate._next.worker import Worker

with Worker(current_channel(), resume_from=None, sources=[], save=NoSave()) as w:
    for i in w.steps(Target(Commits(), 2)):
        w.emit("loss", float(i))
    w.stopped(completed=True, error=None)
"""

# A worker that blocks until killed.
BLOCKER = "import time, runstate; runstate.current_channel(); time.sleep(30)"


def command(code):
    return Command([sys.executable, "-c", code], env={})


def test_subprocess_attaches_to_same_run_and_is_reaped(tmp_path):
    """The child writes on the same run's log, across processes; its reap writes the
    launch's ``terminated``, and the unclaimed run reads ``completed``."""
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("run-1", command(WORKER))
        assert h.wait() == 0
    ch = launcher.attach_channel("run-1")
    assert ch.latest(Topic.VALUE, "loss").body["value"] == 0.5
    launched = ch.latest(Topic.SUPERVISOR_LAUNCHED).body
    assert launched["handle"].startswith("local://") and launched["status"] == "running"
    term = ch.latest(Topic.SUPERVISOR_TERMINATED).body
    assert (term["reason"], term["exit_code"]) == ("exited", 0)
    r = peek_terminal(ch)
    assert (r.outcomes, r.tier) == ({Outcome.COMPLETED}, Tier.TERMINATED)


def test_a_staged_worker_child_claims_commits_and_completes(tmp_path):
    """The staged Worker in a child: its claim names its launch, its nodes land on
    the shared log, and the verdict is its own exit, which its exit-0 reap does not
    change."""
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("run-w", command(STAGED))
        assert h.wait(timeout=60) == 0
    ch = launcher.attach_channel("run-w")
    assert ch.latest(Topic.LIFECYCLE_STARTED).request_id == h.launch_id
    assert len(ch.read(topics=[Topic.LIFECYCLE_COMMIT])) == 2
    assert ch.read(topics=[Topic.SUPERVISOR_DIED]) == []
    r = peek_terminal(ch)
    assert (r.outcomes, r.tier) == ({Outcome.COMPLETED}, Tier.EXIT)


def test_handle_is_alive_then_terminate_records_killed(tmp_path):
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("run-2", command(BLOCKER))
        assert h.is_alive() is True
        h.terminate()
        h.wait()
        assert h.is_alive() is False
    ch = launcher.attach_channel("run-2")
    term = ch.latest(Topic.SUPERVISOR_TERMINATED).body
    assert (term["reason"], term["signal"]) == ("killed", 15)
    r = peek_terminal(ch)
    assert (r.outcomes, r.tier) == ({Outcome.KILLED}, Tier.TERMINATED)


def test_reap_is_idempotent(tmp_path):
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("run-3", command(WORKER))
        h.wait()
        h.wait()  # a second reap writes nothing more
    ch = launcher.attach_channel("run-3")
    assert len(ch.read(topics=[Topic.SUPERVISOR_TERMINATED])) == 1
