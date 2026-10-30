"""The checkpoint recipe, staged (docs/specs/commits-and-lineage.md §6; plan Task 13):
the save policies, which the Worker calls at the two moments it chooses, and the
choice strategies, which pick a complete checkpoint to resume from.

The checkpoints here are a caller's own objects, ``Ckpt``, holding ``node`` and
the state the node commits: the iteration count of a toy training loop. The
Worker's own moments are ``tests/test_next_worker.py``'s and
``tests/test_next_exit.py``'s; these test the recipe that composes them.
"""

from contextlib import nullcontext
from dataclasses import dataclass

import pytest

from runstate._next.checkpoint import Every, MostProgress, OnHeadLineage
from runstate._next.observables import AtNode, LatestClaimHead, prefix_len
from runstate._next.vocabulary.coordinate import Commits, Target
from runstate._next.vocabulary.payloads import Topic
from runstate._next.worker import Worker
from tests.next_helpers import Interloping
from tests.test_next_subscriptions import end
from tests.test_next_worker import worker


@dataclass(frozen=True)
class Ckpt:
    """A caller's checkpoint: the node it was saved at, and the state that node
    commits, here the loop's iteration count."""

    node: int
    iterations: int


class Store:
    """A checkpoint store: one location, written by the save policy's ``fn`` with
    the training loop's state at the moment it is called."""

    def __init__(self):
        self.iterations = 0
        self.saved: list[Ckpt] = []

    def save(self, node):
        self.saved.append(Ckpt(node, self.iterations))


class Recording:
    """A caller's own ``SavePolicy``: it records every moment it is called at."""

    def __init__(self):
        self.calls = []

    def after(self, node, *, commits, exit):
        self.calls.append((node, commits, exit))


def commits_at(ch, node):
    return prefix_len(ch, Commits(), head=AtNode(node))


def train(ch, store, *, resume_from, until, save):
    """The recipe's training loop: claim, load, assert the load, drive, and leave
    the exit to the ``with`` block (a preempted, extendable exit)."""
    with Worker(ch, resume_from=resume_from, sources=[], save=save) as w:
        loaded = resume_from if isinstance(resume_from, Ckpt) else Ckpt(0, 0)
        assert loaded.node == (w.resume_node or 0)
        store.iterations = loaded.iterations
        for _ in w.steps(until):
            store.iterations += 1
            w.emit("loss", 1.0 / store.iterations)


# ----- the save policies (§6, "Save") ------------------------------------------------


def test_every_k_counts_commits_and_saves_at_every_extendable_exit(open_run):
    ch, store = open_run(), Store()
    save = Every(3, store.save)
    train(ch, store, resume_from=None, until=Target(Commits(), 7), save=save)
    exit_ = ch.latest(Topic.LIFECYCLE_STOPPED).seq
    assert [commits_at(ch, c.node) for c in store.saved] == [3, 6, 7]
    assert store.saved[-1].node == exit_  # the preempted chunk's own exit
    # a resumed chunk counts Commits() from the log, not from its own start
    resumed = store.saved[-1]
    train(
        open_run(), store, resume_from=resumed, until=Target(Commits(), 10), save=save
    )
    assert [commits_at(ch, c.node) for c in store.saved] == [3, 6, 7, 9, 10]
    # a torn exit and a closed one save nothing
    with worker(open_run(), resume_from=LatestClaimHead(), save=save) as w:
        w.commit()  # commits 11
        w.discard()
    with worker(open_run(), resume_from=LatestClaimHead(), save=save) as w:
        w.stopped(completed=True, error=None)
    assert len(store.saved) == 5


def test_a_callers_save_policy_is_called_at_each_moment_and_nowhere_else(open_run):
    """Immediately after each commit lands, and after an extendable exit lands:
    never at a heartbeat, a rewind, a value, a lost exit CAS's retry, a torn or a
    closed exit, or by a released worker."""
    obs = open_run()
    ip = Interloping(open_run(), obs)
    policy = Recording()
    w = worker(ip, save=policy)
    w.commit()
    c1 = obs.latest(Topic.LIFECYCLE_COMMIT).seq
    w.heartbeat()
    w.emit("loss", 1.0)
    w.rewind(AtNode(c1))
    w.commit()
    c2 = obs.latest(Topic.LIFECYCLE_COMMIT).seq
    ip.arm({"value": 0, "t": 0.0}, topic=Topic.VALUE, name="other")
    w.stopped(completed=False, error=None)  # its first CAS loses
    exit_ = obs.latest(Topic.LIFECYCLE_STOPPED).seq
    assert ip.fired
    assert policy.calls == [(c1, 1, False), (c2, 2, False), (exit_, 2, True)]
    for leave in ("discard", "error", "release", "complete"):
        policy.calls.clear()
        with pytest.raises(RuntimeError) if leave == "error" else nullcontext():
            with worker(open_run(), resume_from=LatestClaimHead(), save=policy) as w:
                if leave == "discard":
                    w.discard()
                elif leave == "error":
                    raise RuntimeError("boom")
                elif leave == "complete":
                    w.stopped(completed=True, error=None)
                else:
                    end(obs)  # a death report the next drain reads
                    w.commit()
        assert policy.calls == [], leave


def test_the_save_policy_names_the_right_node(open_run):
    """The node a save names commits exactly the saved state: its
    ``count(Commits(), node)`` is the state's iteration count, through a resume and
    a rewind to a checkpoint."""
    ch, store = open_run(), Store()
    save = Every(2, store.save)
    train(ch, store, resume_from=None, until=Target(Commits(), 5), save=save)
    train(
        open_run(),
        store,
        resume_from=store.saved[1],
        until=Target(Commits(), 7),
        save=save,
    )
    # a rewind to a checkpoint, loading its state, then on from there
    with Worker(open_run(), resume_from=store.saved[-1], sources=[], save=save) as w:
        store.iterations = store.saved[-1].iterations
        ckpt = store.saved[0]
        w.rewind(ckpt)
        assert ckpt.node == w.resume_node
        store.iterations = ckpt.iterations
        for _ in w.steps(Target(Commits(), ckpt.iterations + 3)):
            store.iterations += 1
    assert len(store.saved) >= 6
    for c in store.saved:
        assert commits_at(ch, c.node) == c.iterations, c


# ----- the choice strategies (§6, "Choosing a checkpoint") -------------------------


def history(open_run):
    """Three commits, each saved; a rewind to the first; one more, saved. Each
    commit emits one ``loss``, so the counts are 1, 2, 3 on the abandoned branch,
    and 1, 2 on the head's."""
    ch, store = open_run(), Store()
    w = worker(ch, save=Every(1, store.save))
    for _ in range(3):
        store.iterations += 1
        w.emit("loss", 0.0)
        w.commit()
    w.rewind(store.saved[0])
    store.iterations = store.saved[0].iterations
    store.iterations += 1
    w.emit("loss", 0.0)
    w.commit()
    return ch, w, store.saved


CHOICES = [OnHeadLineage(), MostProgress("loss")]


@pytest.mark.parametrize("choice", CHOICES, ids=["on_head_lineage", "most_progress"])
def test_resume_from_takes_the_checkpoint_itself_and_choose_returns_the_callers_type(
    open_run, choice
):
    ch, w, saved = history(open_run)
    w.stopped(completed=False, error=None)
    candidates = list(saved)
    chosen = choice.choose(ch, candidates, head=LatestClaimHead())
    assert any(chosen is c for c in candidates)  # the caller's own object
    assert isinstance(chosen, Ckpt)
    w2 = worker(open_run(), resume_from=chosen)  # taken unconverted
    assert w2.resume_node == chosen.node
    assert ch.latest(Topic.LIFECYCLE_STARTED).body["resume_from"] == chosen.node


def test_the_load_assertion_fires_on_a_mismatch(open_run):
    """The recipe's order: choose, construct the Worker with the checkpoint, which
    claims, load, then ``assert loaded.node == w.resume_node``; and the same after
    ``w.rewind(ckpt)``. A consumer that loads from a mutable location after
    claiming loads another checkpoint, and the assertion catches it."""
    ch, w, saved = history(open_run)
    w.stopped(completed=False, error=None)
    chosen = OnHeadLineage().choose(ch, saved, head=LatestClaimHead())
    w2 = worker(open_run(), resume_from=chosen)
    loaded = saved[0]  # what the mutable file holds now: another node
    assert loaded is not chosen
    with pytest.raises(AssertionError):
        assert loaded.node == w2.resume_node
    w2.rewind(saved[1])
    assert saved[1].node == w2.resume_node
    with pytest.raises(AssertionError):
        assert chosen.node == w2.resume_node


@pytest.mark.parametrize("choice", CHOICES, ids=["on_head_lineage", "most_progress"])
def test_both_choice_policies_select_only_complete_checkpoints(open_run, choice):
    """A checkpoint whose node is above the head's w is not complete: its ancestors
    and values need not be readable there. Both strategies pass over it, however
    much progress it holds."""
    ch, store = open_run(), Store()
    w = worker(ch, save=Every(1, store.save))
    for _ in range(2):
        w.emit("loss", 0.0)
        w.commit()
    head = LatestClaimHead().resolve(ch)  # resolved before the later saves
    for _ in range(2):
        w.emit("loss", 0.0)
        w.commit()
    candidates = list(store.saved)
    assert choice.choose(ch, candidates, head=head) is candidates[1]
    assert choice.choose(ch, candidates[2:], head=head) is None
    assert choice.choose(ch, [], head=LatestClaimHead()) is None


def test_on_head_lineage_respects_a_rewind_and_most_progress_may_not(open_run):
    ch, w, saved = history(open_run)
    c1, c2, c3, c4 = saved
    head = LatestClaimHead()
    assert OnHeadLineage().choose(ch, saved, head=head) is c4  # nearest the head
    assert OnHeadLineage().choose(ch, [c2, c3], head=head) is None  # abandoned
    assert OnHeadLineage().choose(ch, [c3, c1], head=head) is c1
    assert MostProgress("loss").choose(ch, saved, head=head) is c3  # abandoned
    # a tie in progress goes to the higher node seq
    w.rewind(c2)
    w.emit("loss", 0.0)
    w.commit()  # c5: count 3, as c3's
    c5 = Ckpt(ch.latest(Topic.LIFECYCLE_COMMIT).seq, 3)
    assert MostProgress("loss").choose(ch, [c3, c5, c1], head=head) is c5
    assert MostProgress("loss").choose(ch, [c5, c3], head=head) is c5
    assert OnHeadLineage().choose(ch, [c1, c2, c5], head=head) is c5
    # the empty lineage holds no checkpoint
    w.rewind(None)
    assert OnHeadLineage().choose(ch, saved, head=head) is None
