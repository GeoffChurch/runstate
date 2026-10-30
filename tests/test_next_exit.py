"""The staged 0.4.0 Worker's exit and ``discard()`` (docs/specs/commits-and-lineage.md
§2 "Nodes", §4 "discard()", "The exit", §6 "Save"; plan Task 11).

The exit is the claim's last node: closed when it completed, torn when it discarded
its open iteration (every error exit, and any exit after ``discard()``), and
extendable otherwise, so the next claim may resume from it. It is written by a
compare-and-append at a tail its drain has fully read, and names in ``honored``
every stop pending at the exit and every stop that held during the claim.

``test_interop_stop_from_latch_and_honored`` (§9 item 8) is in
``tests/test_next_interop.py``.
"""

import sys

import pytest

from runstate._next.checkpoint import Every
from runstate._next.claims import CAS_ATTEMPTS, endings
from runstate._next.launcher import Command, LocalLauncher, report_death
from runstate._next.observables import (
    AtNode,
    LatestClaimHead,
    Tier,
    live_episode,
    peek_terminal,
    prefix_len,
    series,
    undischarged_stops,
)
from runstate._next.vocabulary.coordinate import Commits, Target
from runstate._next.vocabulary.payloads import Outcome, Topic
from runstate._next.walk import NodeKind, kind_at
from runstate._next.worker import WorkerStarvedError
from tests.next_helpers import Interloping, Wrapping
from tests.test_next_death_reports import dead_handle
from tests.test_next_worker import (
    Clock,
    Recording,
    conforms,
    died_rec,
    nodes,
    records,
    send_stop,
    send_withdraw,
    worker,
)


def exits(ch):
    """The ``lifecycle.stopped`` records, in seq order."""
    return ch.read(topics=[Topic.LIFECYCLE_STOPPED])


def claim_of(ch):
    return ch.latest(Topic.LIFECYCLE_STARTED).seq


def kind(ch, seq):
    return kind_at(ch.read(after=seq - 1, limit=1)[0])


# ----- the exit node (§4, "The exit") ------------------------------------------------


def test_every_exit_kind_writes_its_node(open_run):
    """Completed, preempted (``__exit__`` with no exception), errored (``__exit__``
    with one) and after ``discard()``: each claim's exit is its last node, its
    ``parent`` the position at the exit."""
    ch = open_run()

    with worker(open_run()) as w:
        w.commit()
        w.stopped(completed=True, error=None)
    with worker(open_run(), resume_from=None) as w:
        w.commit()  # preempted: __exit__ with no exception
    with pytest.raises(ValueError):
        with worker(open_run()) as w:
            w.commit()
            raise ValueError("boom")
    with worker(open_run()) as w:
        w.commit()
        w.emit("loss", 1.0)
        w.discard()

    claims = [e.seq for e in ch.read(topics=[Topic.LIFECYCLE_STARTED])]
    commits = [e.seq for e in ch.read(topics=[Topic.LIFECYCLE_COMMIT])]
    common = {"honored": [], "fired": [], "expired": [], "t": 0.0}
    assert [(e.body, e.name, e.request_id) for e in exits(ch)] == [
        (
            {"completed": c, "error": err, "claim_seq": k, "parent": p, "values": v}
            | common,
            None,
            None,
        )
        for c, err, k, p, v in [
            (True, None, claims[0], commits[0], []),
            (False, None, claims[1], commits[1], []),
            (False, "boom", claims[2], commits[2], None),
            (False, None, claims[3], commits[3], None),
        ]
    ]
    # each exit is its claim's last record
    for claim, e in zip(claims[1:], exits(ch)):
        assert claim > e.seq
    conforms(ch)


def test_a_second_stopped_and_the_with_exit_write_nothing(open_run):
    """The first exit stands: a second ``stopped()``, and ``__exit__`` after an
    explicit one, write nothing."""
    ch = open_run()
    with worker(ch) as w:
        for _ in w.steps(Target(Commits(), 3)):
            pass
        w.stopped(completed=True, error=None)
        w.stopped(completed=False, error="x")
    assert len(exits(ch)) == 1
    assert exits(ch)[0].body["completed"] is True
    last = ch.latest(Topic.LIFECYCLE_COMMIT).seq
    assert exits(ch)[0].body["parent"] == last


def test_stopped_refuses_a_completed_exit_with_an_error(open_run):
    ch = open_run()
    w = worker(ch)
    before = ch.last_seq()
    with pytest.raises(ValueError):
        w.stopped(completed=True, error="x")
    assert ch.last_seq() == before


def test_an_exit_that_kept_its_iteration_commits_post_loop_values(open_run):
    ch = open_run()
    with worker(ch) as w:
        for i in w.steps(Target(Commits(), 2)):
            w.emit("loss", float(i))
        w.emit("loss", 9.0)  # after the loop: committed by the exit
        post = ch.last_seq()
    assert exits(ch)[0].body["values"] == [post]
    head = LatestClaimHead()
    assert [e.value for e in series(ch, "loss", head=head)] == [0.0, 1.0, 9.0]
    assert prefix_len(ch, Commits(), head=head) == 2  # an exit adds no iteration


def test_an_error_exit_and_an_exit_after_discard_are_torn(open_run):
    ch = open_run()
    with pytest.raises(RuntimeError):
        with worker(ch) as w:
            w.commit()
            w.emit("loss", 1.0)
            raise RuntimeError("boom")
    errored = exits(ch)[-1].seq
    with worker(ch, resume_from=LatestClaimHead()) as w:
        w.emit("loss", 2.0)
        w.discard()
    discarded = exits(ch)[-1].seq
    assert kind(ch, errored) is NodeKind.TORN
    assert kind(ch, discarded) is NodeKind.TORN
    assert len(nodes(ch)) == 1  # the one commit both claims stand on
    first = ch.latest(Topic.LIFECYCLE_COMMIT).seq
    # nothing extends a torn exit: the next claim continues its parent
    w = worker(ch, resume_from=LatestClaimHead())
    assert w.resume_node == first
    with pytest.raises(ValueError):
        w.rewind(AtNode(discarded))
    assert prefix_len(ch, "loss", head=AtNode(errored)) == 0  # discarded values


def test_a_completed_exit_after_discard_is_closed_with_null_values(open_run):
    ch = open_run()
    with worker(ch) as w:
        w.commit()
        w.emit("loss", 1.0)
        w.discard()
        w.stopped(completed=True, error=None)
    e = exits(ch)[0]
    assert (e.body["completed"], e.body["values"]) == (True, None)
    assert kind(ch, e.seq) is NodeKind.CLOSED
    with pytest.raises(ValueError, match="closed"):
        worker(ch, resume_from=LatestClaimHead())


def test_after_discard_commit_and_heartbeat_return_true_and_write_nothing(open_run):
    """No drain either (§4): a drain could register and bind a lease the exit
    would void at once, losing a request the next claim could serve."""
    ch = open_run()
    w = worker(ch)
    w.discard()
    send_stop(open_run(), "s")
    open_run().send({}, topic="control.frobnicate", request_id="r")
    before = records(ch)
    assert w.commit() is True
    assert w.heartbeat() is True
    assert w.stop_pending is True
    assert list(w.steps(None)) == []
    assert records(ch) == before  # nothing drained, refused or written
    w.discard()  # a second call does nothing
    w.stopped(completed=False, error=None)
    e = exits(ch)[0]
    assert e.body["values"] is None
    assert e.body["honored"] == ["s"]  # the exit drains: its stops are named


def test_after_discard_emit_set_include_and_rewind_raise(open_run):
    """The worker discarded the iteration itself, so a later write is its own bug,
    and could never be committed (§4)."""
    ch = open_run()
    w = worker(ch, sources=["lr"])
    w.emit("loss", 1.0)
    seq = ch.last_seq()
    w.commit()
    w.discard()
    before = ch.last_seq()
    for call in (
        lambda: w.emit("loss", 1.0),
        lambda: w.set("lr", 0.1),
        lambda: w.include(seq),
        lambda: w.rewind(None),
    ):
        with pytest.raises(RuntimeError):
            call()
    assert ch.last_seq() == before


def test_a_preempted_exit_is_extendable_and_the_next_claim_keeps_its_values(open_run):
    ch = open_run()
    with worker(ch) as w:
        for i in w.steps(Target(Commits(), 2)):
            w.emit("loss", float(i))
        w.emit("loss", 2.0)  # the chunk's post-loop value
    exit_ = exits(ch)[0].seq
    assert kind(ch, exit_) is NodeKind.EXTENDABLE
    with worker(ch, resume_from=LatestClaimHead()) as w2:
        assert w2.resume_node == exit_
        for i in w2.steps(Target(Commits(), 3)):
            w2.emit("loss", 3.0)
    assert nodes(ch)[-1]["parent"] == exit_
    head = LatestClaimHead()
    assert [e.value for e in series(ch, "loss", head=head)] == [0.0, 1.0, 2.0, 3.0]


def test_commits_count_iterations_across_resume_rewind_and_an_extended_exit(open_run):
    ch = open_run()
    with worker(ch) as w:
        assert list(w.steps(Target(Commits(), 3))) == [0, 1, 2]
    second = ch.read(topics=[Topic.LIFECYCLE_COMMIT])[1].seq
    with worker(ch, resume_from=LatestClaimHead()) as w:
        assert list(w.steps(Target(Commits(), 4))) == [3]  # the exit adds none
        w.rewind(AtNode(second))
        assert list(w.steps(Target(Commits(), 4))) == [2, 3]
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 4


# ----- honored (§4, "The exit", "honored") --------------------------------------------


def test_a_lost_exit_cas_redrains_and_honors_a_stop_first_drained_there(open_run):
    """A stop that lands just before the exit's compare-and-append: the CAS loses,
    the exit's step runs again, drains it, and names it (§4, step 4)."""
    obs = open_run()
    ch = Interloping(open_run(), obs)
    w = worker(ch)
    w.commit()
    ch.arm({}, topic=Topic.CONTROL_STOP, request_id="race")
    w.stopped(completed=False, error=None)
    assert ch.fired
    assert len(exits(obs)) == 1  # the lost CAS wrote nothing
    assert exits(obs)[0].body["honored"] == ["race"]
    assert undischarged_stops(obs) == []


def test_an_exit_whose_cas_never_wins_raises_worker_starved_and_the_claim_stays_held(
    open_run,
):
    """M3: an exit that loses every CAS gives up after ``CAS_ATTEMPTS``, raising
    out of ``stopped()``, typed and naming the claim: no exit is written, and the
    claim is held for a death report to end."""
    obs = open_run()
    ch = Interloping(open_run(), obs)
    w = worker(ch)
    w.commit()
    claim = obs.latest(Topic.LIFECYCLE_STARTED)
    ch.arm({"value": 1.0, "t": 1.0}, topic=Topic.VALUE, name="x", times=None)
    with pytest.raises(WorkerStarvedError) as exc:
        w.stopped(completed=False, error=None)
    assert (exc.value.step, exc.value.claim_seq, exc.value.attempts) == (
        "exit",
        claim.seq,
        CAS_ATTEMPTS,
    )
    assert exits(obs) == []
    assert endings(obs, claim, obs.last_seq()) == []


def test_a_starved_exit_fences_every_call_but_a_retried_stopped(open_run):
    """§4, "An exit that starves": the exit's drain passed over a subscribe, and its
    CAS then starved. Every call but ``stopped()`` is fenced, writing nothing, so no
    watermark of the claim (a commit's or a heartbeat's ``consumed_seq``) claims the
    subscribe the failed exit passed over; a retried ``stopped()``, on a channel
    gone quiet, lands the exit, and the subscribe stays live for the next claim."""
    obs = open_run()
    ch = Interloping(open_run(), obs)
    w = worker(ch, sources=["loss"])
    w.set("loss", 1.0)
    w.commit()
    late = obs.send(
        {"every": {"commits": 1}},
        topic=Topic.CONTROL_SUBSCRIBE,
        name="loss",
        request_id="late",
    )
    v = obs.send({"value": 2.0, "t": 0.0}, topic=Topic.VALUE, name="x")
    noise = {"value": 1.0, "t": 1.0}
    ch.arm(noise, topic=Topic.VALUE, name="noise", times=CAS_ATTEMPTS)
    with pytest.raises(WorkerStarvedError) as exc:
        w.stopped(completed=False, error=None)
    assert exc.value.step == "exit" and exits(obs) == []
    before = obs.last_seq()
    assert w.commit() is True and w.heartbeat() is True
    w.emit("x", 3.0)
    w.set("loss", 4.0)
    w.include(v)
    w.rewind(None)
    w.discard()
    assert list(w.serve()) == [] and w.retire() is True
    assert obs.last_seq() == before  # fenced: nothing written
    assert w.stop_pending is True and w.pinned is False
    w.stopped(completed=False, error=None)  # the retry, on a quiet channel
    (exit_,) = exits(obs)
    claim = obs.latest(Topic.LIFECYCLE_STARTED).seq
    assert exit_.body["claim_seq"] == claim and exit_.body["values"] == []
    assert exit_.body["parent"] == obs.latest(Topic.LIFECYCLE_COMMIT).seq
    beats = obs.read(topics=[Topic.LIFECYCLE_COMMIT, Topic.LIFECYCLE_HEARTBEAT])
    assert all(e.body["consumed_seq"] < late for e in beats)
    w.stopped(completed=False, error=None)  # a second call does nothing
    assert len(exits(obs)) == 1
    w2 = worker(open_run(), resume_from=LatestClaimHead(), sources=["loss"])
    w2.set("loss", 5.0)
    w2.commit()  # the next claim serves the subscribe the exit passed over
    assert obs.latest(Topic.LIFECYCLE_COMMIT).body["fired"] == ["late"]


@pytest.mark.parametrize(
    "exc", [KeyboardInterrupt(), SystemExit(), RuntimeError()], ids=repr
)
def test_an_exception_with_no_message_names_its_type_as_the_error(open_run, exc):
    """M2: ``__exit__`` never writes ``error: ""``. An exception whose message is
    empty is named by its type."""
    ch = open_run()
    with pytest.raises(type(exc)):
        with worker(ch) as w:
            w.commit()
            raise exc
    assert exits(ch)[0].body["error"] == type(exc).__name__
    conforms(ch)


def test_one_exit_honors_every_pending_stop(open_run):
    """Every stop pending at the exit is honored, the one that held and the one
    that did not, and so is one sent after the last drain: none haunts the next
    claim."""
    ch = open_run()
    obs = open_run()
    send_stop(obs, "s1", **{"from": {"commits": 2}})
    send_stop(obs, "s2", **{"from": {"commits": 5}})
    with worker(ch) as w:
        assert list(w.steps(Target(Commits(), 10))) == [0, 1]
        send_stop(obs, "late")  # after the last drain
    assert set(exits(ch)[0].body["honored"]) == {"s1", "s2", "late"}  # a set (§3)
    assert undischarged_stops(obs) == []
    with worker(ch, resume_from=LatestClaimHead()) as w:
        assert list(w.steps(Target(Commits(), 4))) == [2, 3]


def test_steps_ends_on_a_stop_and_the_exit_names_it(open_run):
    ch = open_run()
    send_stop(open_run(), "s1", **{"from": {"commits": 3}})
    with worker(ch) as w:
        assert list(w.steps(Target(Commits(), 10))) == [0, 1, 2]
    body = exits(ch)[0].body
    assert (body["completed"], body["error"], body["honored"]) == (False, None, ["s1"])
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 3


def test_a_withdrawal_after_true_is_too_late_and_honored_names_the_stop(open_run):
    ch = open_run()
    obs = open_run()
    send_stop(obs, "s")
    with worker(ch) as w:
        assert w.commit() is True
        send_withdraw(obs, "s")  # too late: the claim stops
        assert w.commit() is True
    assert exits(ch)[0].body["honored"] == ["s"]


def test_honored_names_a_stop_that_held_only_at_a_heartbeat(open_run):
    ch = open_run()
    obs = open_run()
    clock = Clock()
    send_stop(obs, "s", **{"from": {"time_seconds": 10}})
    with worker(ch, now=clock) as w:
        assert w.commit() is False
        clock.t = 10.0
        assert w.heartbeat() is True  # held here, at no node
        send_withdraw(obs, "s")  # even withdrawn since
    assert exits(ch)[0].body["honored"] == ["s"]


def test_a_stop_sent_before_any_worker_is_honored_exactly_once(open_run):
    """A stop sent before any worker is drained by the first claim, honored by its
    exit, and spent by name: the next claim runs free."""
    ch = open_run()
    send_stop(open_run(), "pre")
    with worker(ch) as w:
        assert list(w.steps(Target(Commits(), 3))) == [0]
    assert exits(ch)[0].body["honored"] == ["pre"]
    assert undischarged_stops(ch) == []
    with worker(ch, resume_from=LatestClaimHead()) as w:
        assert list(w.steps(Target(Commits(), 3))) == [1, 2]


def test_a_stop_after_an_exit_blips_the_next_claim_once(open_run):
    """A stop sent while the run is down is not dropped: the next claim answers it
    at its first node, and exactly once."""
    ch = open_run()
    with worker(ch) as w:
        assert list(w.steps(Target(Commits(), 2))) == [0, 1]
    send_stop(open_run(), "after")
    with worker(ch, resume_from=LatestClaimHead()) as w:
        assert list(w.steps(Target(Commits(), 5))) == [2]  # the blip
    with worker(ch, resume_from=LatestClaimHead()) as w:
        assert list(w.steps(Target(Commits(), 5))) == [3, 4]


def test_a_stop_no_exit_honored_rearms_on_the_next_claim(open_run):
    """A claim that crashed (no exit, its handle dead on this host) answered no
    stop: the next claim's ``report_if_dead`` reports it, and the stop, still
    pending by name, re-arms and is honored at its node."""
    ch = open_run()
    body = {"handle": dead_handle(), "t": 0.0, "resume_from": None, "sources": []}
    ch.send(body, topic=Topic.LIFECYCLE_STARTED)
    send_stop(ch, "s1", **{"from": {"commits": 3}})
    with worker(open_run()) as w:
        assert w.claimed
        assert list(w.steps(Target(Commits(), 10))) == [0, 1, 2]
    assert exits(ch)[0].body["honored"] == ["s1"]


# ----- the save policy at the exit (§6, "Save") ---------------------------------------


def test_the_save_policy_runs_after_an_extendable_exit_and_never_after_a_torn_or_closed_one(
    open_run,
):
    ch = open_run()
    save = Recording(open_run())
    with worker(ch, save=save) as w:  # preempted: extendable
        w.commit()
    c, exit_ = ch.latest(Topic.LIFECYCLE_COMMIT).seq, exits(ch)[-1].seq
    assert save.calls == [(c, 1, False, c), (exit_, 1, True, exit_)]

    for end_kind in ("completed", "errored", "discarded"):
        save = Recording(open_run())
        try:
            with worker(ch, save=save) as w:
                if end_kind == "completed":
                    w.stopped(completed=True, error=None)
                elif end_kind == "errored":
                    raise KeyError("boom")
                else:
                    w.discard()
        except KeyError:
            pass
        assert save.calls == [], end_kind


def test_a_save_policy_that_raises_leaves_its_node_committed_and_the_exit_torn(
    open_run,
):
    """Review Focus 4, after a commit (§6, "A save that raises"): the raise leaves
    ``commit()`` and ends the claim, so ``__exit__`` writes an errored, torn exit
    whose ``parent`` is that node, which stays extendable."""

    class DiskFull:
        def after(self, node, *, commits, exit):
            if commits == 2:
                raise OSError("disk full")

    ch = open_run()
    with pytest.raises(OSError, match="disk full"):
        with worker(ch, save=DiskFull()) as w:
            for i in w.steps(Target(Commits(), 5)):
                w.emit("loss", float(i))
    second = ch.read(topics=[Topic.LIFECYCLE_COMMIT])[-1].seq
    assert len(nodes(ch)) == 2  # the node stands
    e = exits(ch)[0]
    assert (e.body["error"], e.body["values"], e.body["parent"]) == (
        "disk full",
        None,
        second,
    )
    assert kind(ch, second) is NodeKind.EXTENDABLE
    assert worker(ch, resume_from=LatestClaimHead()).resume_node == second


# A child whose Every(2) save writes each node it saves to a file, its checkpoint
# store, until the exit, where the save raises: the store is full.
SAVE_RAISES_AT_THE_EXIT = """
import sys
from runstate import current_channel
from runstate._next.checkpoint import Every
from runstate._next.claims import CAS_ATTEMPTS, endings
from runstate._next.vocabulary.coordinate import Commits, Target
from runstate._next.worker import Worker

store, exiting = sys.argv[1], []

def save(node):
    if exiting:
        raise OSError("disk full")
    with open(store, "a") as f:
        f.write(f"{node}\\n")

with Worker(current_channel(), resume_from=None, sources=[], save=Every(2, save)) as w:
    for i in w.steps(Target(Commits(), 3)):
        w.emit("loss", float(i))
    exiting.append(True)
"""


def test_a_save_that_raises_after_an_extendable_exit_leaves_the_exit_and_the_next_resume_branches(
    tmp_path,
):
    """Review Focus 4, at the exit (§6, "A save that raises"; plan, open question
    17): the claim has already ended, so the exit stands with its own verdict and
    nothing more is written; the process's failure is on the external plane, its
    launch's ``supervisor.terminated``; the next claim resumes from the newest
    checkpoint the store holds, an older node, and branches there."""
    store = tmp_path / "checkpoints"
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch(
            "run",
            Command(
                [sys.executable, "-c", SAVE_RAISES_AT_THE_EXIT, str(store)], env={}
            ),
        )
        assert h.wait(timeout=60) != 0
        ch = launcher.attach_channel("run")
    verdict = peek_terminal(ch)
    assert (verdict.tier, verdict.outcomes) == (Tier.EXIT, {Outcome.PREEMPTED})
    claim = claim_of(ch)
    exit_ = exits(ch)[0]
    assert exit_.body["claim_seq"] == claim
    # the exit is the claim's last record: only the launch's end follows it
    assert [e.topic for e in ch.read(after=exit_.seq)] == [Topic.SUPERVISOR_TERMINATED]
    terminated = ch.latest(Topic.SUPERVISOR_TERMINATED).body
    assert terminated["exit_code"] not in (None, 0)
    assert ch.read(topics=[Topic.SUPERVISOR_DIED]) == []

    saved = [int(line) for line in store.read_text().split()]
    p = ch.read(topics=[Topic.LIFECYCLE_COMMIT])[1].seq  # the second commit
    assert saved == [p]
    w = worker(ch, resume_from=AtNode(saved[-1]))  # the recipe's newest checkpoint
    assert w.resume_node == p
    w.commit()
    assert nodes(ch)[-1]["parent"] == p  # a branch: the exit is not on it
    ch.close()


# ----- the fence at the exit (§4, "A released worker fences itself") ------------------


def test_a_released_worker_latches_before_the_batch_that_holds_the_report(open_run):
    """A stop and a death report naming the claim land in one batch above the
    drain's watermark: the drain reads the report first, so the worker honors no
    stop and writes no exit (``__exit__`` included), and the next claim honors the
    stop."""
    ch = open_run()
    obs = open_run()
    with worker(ch) as w:
        w.commit()
        send_stop(obs, "s")
        obs.send(died_rec(claim_of(obs)), topic=Topic.SUPERVISOR_DIED)
    assert w.released
    assert exits(obs) == []
    assert [e.request_id for e in undischarged_stops(obs)] == ["s"]
    with worker(ch, resume_from=LatestClaimHead()) as w2:
        assert w2.commit() is True
    assert exits(obs)[0].body["honored"] == ["s"]


def test_a_displaced_workers_exit_and_beats_write_nothing(open_run):
    """A worker displaced by a death report (a supervisor's evidence, then a new
    claim) is fenced: its heartbeats, commits and exit write nothing, so the
    successor's claim stays live and its progress is its own."""
    ch = open_run()
    old = worker(ch)
    old.commit()
    report_death(
        ch, claim_of(ch), outcome=Outcome.KILLED, evidence="slurm:x", force=True
    )
    new = worker(open_run(), resume_from=LatestClaimHead())
    new.commit()
    before = records(ch)
    assert old.heartbeat() is True
    assert old.commit() is True
    old.stopped(completed=True, error=None)
    assert records(ch) == before
    assert live_episode(ch) is not None
    assert peek_terminal(ch) is None
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 2


@pytest.mark.parametrize("discarded", [False, True])
def test_after_its_exit_a_worker_writes_nothing_and_says_stop(open_run, discarded):
    """A worker after its own exit is fenced as a released one is (§4, "A worker
    after its own exit is fenced the same way"): it writes nothing,
    ``commit()``/``heartbeat()`` return True, ``stop_pending`` is True, and its
    other calls do nothing, misuse included, even after a ``discard()``, whose
    raising ends with the exit."""
    ch = open_run()
    w = worker(ch, sources=["lr"])
    w.emit("loss", 1.0)
    w.commit()
    stray = ch.send({"value": 2.0, "t": 0.0}, topic=Topic.VALUE, name="loss")
    if discarded:
        w.discard()
    w.stopped(completed=False, error=None)
    with worker(ch, resume_from=LatestClaimHead()) as w2:
        w2.emit("loss", 101.0)
        w2.commit()
        before = records(ch)
        w.emit("loss", -999.0)  # the first claim's, after its exit
        w.emit("lr", 1.0)  # misuse, silently nothing
        w.set("lr", 1.0)
        w.set("loss", 1.0)
        w.include(stray)
        w.rewind(None)
        w.discard()
        w.stopped(completed=True, error=None)
        assert w.commit() is True
        assert w.heartbeat() is True
        assert w.stop_pending is True
        assert list(w.steps(None)) == []
        assert records(ch) == before
    assert [e.value for e in series(ch, "loss", head=LatestClaimHead())] == [1.0, 101.0]


# ----- a commit that raises before its node lands (§4, "commit()", amended) --------


class _CommitLost(Wrapping):
    """A channel handle whose ``lifecycle.commit`` sends raise once ``lost`` is set:
    a write whose outcome the worker cannot know."""

    lost = False

    def send(self, body, *, topic, name=None, request_id=None, expected_seq=None):
        if self.lost and topic == Topic.LIFECYCLE_COMMIT:
            raise ConnectionError("acknowledgement lost")
        return super().send(
            body,
            topic=topic,
            name=name,
            request_id=request_id,
            expected_seq=expected_seq,
        )


def _subscribe_lease(ch):
    """A count lease on ``x``: it expires at its second firing."""
    ch.send(
        {"every": {"commits": 1}, "until": {"count": 2}},
        topic=Topic.CONTROL_SUBSCRIBE,
        name="x",
        request_id="L",
    )


def test_a_commit_whose_sample_raises_tears_its_iteration(open_run):
    """§4, "commit()" (amended 2026-10-30): the evaluation had already counted the
    lease's firing when the sample raised, so a caller that catches the raise and
    carries on must not see the lease expire a firing early. The iteration is torn,
    as by ``discard()``: writes raise, ``commit()`` writes nothing and says stop,
    and the exit is torn, evaluating nothing."""
    ch, obs = open_run(), open_run()
    _subscribe_lease(obs)
    w = worker(ch, sources=["x"])
    w.set("x", object())  # not JSON: its sample raises
    with pytest.raises(TypeError):
        w.commit()
    before = obs.last_seq()
    for call in (lambda: w.emit("loss", 1.0), lambda: w.set("x", 1)):
        with pytest.raises(RuntimeError, match="discarded"):
            call()
    assert w.commit() is True and w.heartbeat() is True
    assert w.stop_pending is True and w.pinned is False
    assert obs.last_seq() == before  # nothing drained or written
    w.stopped(completed=False, error=None)
    assert obs.read(topics=[Topic.LIFECYCLE_COMMIT]) == []
    (e,) = exits(obs)
    assert (e.body["values"], e.body["fired"], e.body["expired"]) == (None, [], [])
    assert kind(obs, e.seq) is NodeKind.TORN


def test_a_commit_whose_node_send_raises_tears_its_iteration_and_the_exit_errs(
    open_run,
):
    """The ``lifecycle.commit`` send raises, its outcome unknown: the iteration is
    torn, so the caller's writes raise, and the raise, left to the ``with`` block,
    ends the claim errored and torn, its parent the position, its sample outside
    every lineage."""
    obs = open_run()
    ch = _CommitLost(open_run())
    _subscribe_lease(obs)
    with pytest.raises(ConnectionError):
        with worker(ch, sources=["x"]) as w:
            w.set("x", 1.0)
            ch.lost = True
            try:
                w.commit()
            finally:
                with pytest.raises(RuntimeError, match="discarded"):
                    w.emit("loss", 1.0)
                assert w.commit() is True
    (e,) = exits(obs)
    body = e.body
    assert (body["error"], body["values"], body["parent"]) == (
        "acknowledgement lost",
        None,
        None,
    )
    assert (body["fired"], body["expired"]) == ([], [])
    assert len(obs.read(topics=[Topic.VALUE], name="x")) == 1  # the sample stays
    assert kind(obs, e.seq) is NodeKind.TORN


def test_a_save_that_raises_after_its_node_landed_leaves_the_worker_live(open_run):
    """§6's case, not the torn one: the node stands, and the worker is as it was, so
    a caller that catches the raise and carries on commits the next node on it."""

    class FirstSaveFails:
        calls = 0

        def after(self, node, *, commits, exit):
            self.calls += 1
            if self.calls == 1:
                raise OSError("disk full")

    ch = open_run()
    w = worker(ch, save=FirstSaveFails())
    with pytest.raises(OSError):
        w.commit()
    first = ch.latest(Topic.LIFECYCLE_COMMIT).seq
    w.emit("loss", 1.0)
    assert w.commit() is False
    assert nodes(ch)[-1]["parent"] == first


def test_a_starved_exit_keeps_its_sample_failure_and_its_retry_raises_it(open_run):
    """§4, "The exit", step 3, and "An exit that starves": a sample that fails makes
    the exit errored and torn, and its CAS then starves. ``WorkerStarvedError``
    names the failure as its cause, and the retried ``stopped()``, once its exit
    lands, raises the failure, as the first attempt would have."""
    obs = open_run()
    ch = Interloping(open_run(), obs)
    w = worker(ch, sources=["x"])
    obs.send(
        {"every": {"commits": 1}},
        topic=Topic.CONTROL_SUBSCRIBE,
        name="x",
        request_id="d",
    )
    w.commit()  # registers d; x holds no value, so nothing fires
    w.set("x", object())  # the exit's sample raises
    noise = {"value": 1.0, "t": 1.0}
    ch.arm(noise, topic=Topic.VALUE, name="noise", times=CAS_ATTEMPTS)
    with pytest.raises(WorkerStarvedError) as exc:
        w.stopped(completed=False, error=None)
    assert isinstance(exc.value.__cause__, TypeError)
    assert exits(obs) == []
    with pytest.raises(TypeError):
        w.stopped(completed=False, error=None)  # the retry, on a quiet channel
    (e,) = exits(obs)
    assert e.body["values"] is None and "x" in e.body["error"]
    assert kind(obs, e.seq) is NodeKind.TORN
