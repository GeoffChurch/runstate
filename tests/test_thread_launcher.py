"""ThreadLauncher: the in-process reference launcher (docs/design-v0.2.md §8-9).

Runs the worker target on a thread in the same process, sharing the run's
channel directly (memory backend). Records the process-level lifecycle on the
log: launcher.launched (spawn-intent + handle) at start, launcher.terminated
(manner of death) when the target returns or raises.
"""

import threading

from runstate.launcher import ThreadLauncher
from runstate.observables import peek_terminal


def test_launch_runs_target_and_brackets_with_launcher_lifecycle(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    seen = []

    def target(channel):
        seen.append(channel)
        channel.send({"value": 1}, topic="value", name="loss")

    h = launcher.launch("run-1", target)
    h.wait()

    assert len(seen) == 1
    ch = launcher.open_channel("run-1")
    topics = [e.topic for e in ch.read()]
    # launched brackets the work, terminated closes it
    assert topics[0] == "launcher.launched"
    assert topics[-1] == "launcher.terminated"
    assert "value" in topics

    launched = ch.latest("launcher.launched")
    assert launched.body["handle"].startswith("local://")
    assert launched.body["status"] == "running"

    term = ch.latest("launcher.terminated")
    assert term.body["exit_code"] == 0
    assert term.body["reason"] == "exited"


def test_handle_fields_and_liveness(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    gate = threading.Event()

    def target(channel):
        gate.wait()

    h = launcher.launch("run-2", target)
    assert h.run_id == "run-2"
    assert h.handle.startswith("local://")
    assert h.is_alive() is True  # blocked on the gate
    gate.set()
    h.wait()
    assert h.is_alive() is False


def test_errored_target_records_nonzero_exit(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)

    def target(channel):
        raise RuntimeError("boom")

    h = launcher.launch("run-3", target)
    h.wait()

    assert isinstance(h.exception, RuntimeError)
    ch = launcher.open_channel("run-3")
    term = ch.latest("launcher.terminated")
    assert term.body["exit_code"] == 1
    assert term.body["reason"] == "exited"
    # the observer reads "errored" from the nonzero exit
    assert peek_terminal(ch).outcome == "errored"


def test_target_receives_args(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    got = []

    def target(channel, a, b, *, c):
        got.append((a, b, c))

    h = launcher.launch("run-4", target, args=(1, 2), kwargs={"c": 3})
    h.wait()
    assert got == [(1, 2, 3)]


def test_a_claim_losers_clean_exit_does_not_forge_the_winners_verdict(tmp_path):
    """Two concurrent dispatchers, one run: the loser's launch is the NEWEST on
    the log and its clean exit the newest death — and the winner is still alive.

    The winner is deliberately SLOW to claim, so its ``lifecycle.started`` lands
    *after* the loser's ``launcher.launched``: log position cannot attribute the
    claim to its launch, which is why the correlation id (not the seq, and not
    the handle — in-process threads share one pid, hence one handle) is what
    carries identity here (specs/launcher-record-identity.md).
    """
    from runstate.worker import Worker

    launcher = ThreadLauncher(root=tmp_path)
    claim_now = threading.Event()
    winner_claimed = threading.Event()
    loser_done = threading.Event()
    release_winner = threading.Event()

    def winner(channel):
        claim_now.wait(20)
        with Worker(channel) as w:              # wins the CAS
            winner_claimed.set()
            release_winner.wait(20)             # ...and is still alive at the end
            for _ in w.steps(1):
                pass

    def loser(channel):
        winner_claimed.wait(20)
        with Worker(channel) as w:              # loses the CAS
            assert not w.claimed
            for _ in w.steps(3):
                pass
        loser_done.set()                        # returns cleanly -> Terminated(exited, 0)

    hw = launcher.launch("collide", winner)     # launched FIRST...
    hl = launcher.launch("collide", loser)      # ...but claims after this launch
    claim_now.set()
    assert loser_done.wait(20) and hl.wait() is None
    ch = launcher.open_channel("collide")

    starteds = ch.read(topics=["lifecycle.started"])
    assert len(starteds) == 1                             # exactly one claim
    assert starteds[0].request_id == hw.launch_id         # it names the launch it answers
    assert starteds[0].seq > ch.read(topics=["launcher.launched"])[-1].seq   # after BOTH launches

    terms = ch.read(topics=["launcher.terminated"])
    assert [t.request_id for t in terms] == [hl.launch_id]   # the loser's corpse, honestly recorded
    assert peek_terminal(ch) is None                         # ...and the winner runs on

    release_winner.set()
    hw.wait()
    assert peek_terminal(ch).outcome == "preempted"          # now the winner's own verdict
